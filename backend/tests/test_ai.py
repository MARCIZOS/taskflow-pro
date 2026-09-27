"""AI suggestion flow: gates, human approval, fallback. No network is used."""

import httpx
import pytest

from app.services import ai
from tests.conftest import by_title


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, items):
        self.items = items

    def propose(self, tasks, edges):
        return self.items


class FailingProvider:
    name, model = "broken", "broken-1"

    def propose(self, tasks, edges):
        raise ai.ProviderError("timeout")


def ids(client):
    return {t["title"]: t["id"] for t in client.get("/api/board").json()["tasks"]}


def run(session, provider):
    return ai.suggest(session, provider=provider)


def test_gates_drop_bad_suggestions(seeded, session_factory):
    t = ids(seeded)
    items = [
        # valid: grounded in the Production release description
        {"predecessor_id": t["Security review"], "successor_id": t["Production release"], "confidence": 0.8,
         "reason": "Release waits for security sign-off.", "evidence": "the security review has no open findings"},
        # gate 1: bad format (confidence out of range)
        {"predecessor_id": t["Security review"], "successor_id": t["Deploy to staging"], "confidence": 7,
         "reason": "x y z", "evidence": "security review"},
        # gate 2: unknown task
        {"predecessor_id": 999, "successor_id": t["Security review"], "confidence": 0.5,
         "reason": "made up", "evidence": "pen test"},
        # gate 3: already exists
        {"predecessor_id": t["User acceptance testing"], "successor_id": t["Production release"], "confidence": 0.9,
         "reason": "dup", "evidence": "user acceptance testing is complete"},
        # gate 4: evidence not in the text (hallucinated reason)
        {"predecessor_id": t["Build frontend UI"], "successor_id": t["Security review"], "confidence": 0.7,
         "reason": "UI must be reviewed", "evidence": "the UI needs a security audit first"},
        # gate 5: would create a cycle
        {"predecessor_id": t["Production release"], "successor_id": t["Gather requirements"], "confidence": 0.6,
         "reason": "loop", "evidence": "Go live"},
    ]
    with session_factory() as s:
        out = run(s, FakeProvider(items))
    assert out.proposed == 6 and out.shown == 1
    assert out.dropped == {
        "bad_format": 1, "unknown_task": 1, "duplicate_or_self": 1,
        "evidence_not_found": 1, "would_create_cycle": 1,
    }
    assert out.suggestions[0].predecessor_title == "Security review"


def test_suggestions_are_not_saved_until_accepted(seeded, session_factory):
    t = ids(seeded)
    before = len(seeded.get("/api/board").json()["dependencies"])
    with session_factory() as s:
        out = run(s, FakeProvider([{"predecessor_id": t["Security review"], "successor_id": t["Production release"],
                                    "confidence": 0.8, "reason": "sign-off", "evidence": "security review"}]))
    assert len(seeded.get("/api/board").json()["dependencies"]) == before
    sid = out.suggestions[0].id
    r = seeded.post(f"/api/ai/suggestions/{sid}/accept")
    assert r.status_code == 200
    deps = r.json()["board"]["dependencies"]
    assert len(deps) == before + 1 and deps[-1]["source"] == "ai"
    assert seeded.post(f"/api/ai/suggestions/{sid}/accept").status_code == 409  # already decided


def test_accept_is_revalidated_by_the_engine(seeded, session_factory):
    t = ids(seeded)
    with session_factory() as s:
        out = run(s, FakeProvider([{"predecessor_id": t["Security review"], "successor_id": t["Production release"],
                                    "confidence": 0.8, "reason": "sign-off", "evidence": "security review"}]))
    # Someone adds the same link by hand before the suggestion is accepted.
    seeded.post("/api/dependencies", json={"predecessor_id": t["Security review"], "successor_id": t["Production release"]})
    r = seeded.post(f"/api/ai/suggestions/{out.suggestions[0].id}/accept")
    assert r.status_code == 409
    assert seeded.get("/api/ai/suggestions", params={"status": "stale"}).json()[0]["id"] == out.suggestions[0].id


def test_reject_and_metrics(seeded, session_factory):
    t = ids(seeded)
    with session_factory() as s:
        out = run(s, FakeProvider([
            {"predecessor_id": t["Security review"], "successor_id": t["Production release"], "confidence": 0.8,
             "reason": "sign-off", "evidence": "security review"},
            {"predecessor_id": t["Build frontend UI"], "successor_id": t["Security review"], "confidence": 0.4,
             "reason": "review the backend", "evidence": "code review of the backend API"},
        ]))
    a, b = out.suggestions
    assert seeded.post(f"/api/ai/suggestions/{a.id}/accept").status_code == 200
    assert seeded.post(f"/api/ai/suggestions/{b.id}/reject").status_code == 204
    m = seeded.get("/api/ai/metrics").json()
    assert m == {"shown": 2, "accepted": 1, "rejected": 1, "pending": 0, "precision": 0.5}


def test_keyword_fallback_finds_the_missing_link(seeded):
    r = seeded.post("/api/ai/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "keyword-heuristic"
    pairs = {(s["predecessor_title"], s["successor_title"]) for s in body["suggestions"]}
    assert ("Security review", "Production release") in pairs
    # Existing links are proposed too, but gate 3 filters them out.
    assert body["dropped"].get("duplicate_or_self", 0) > 0


def test_llm_failure_falls_back_to_heuristic(seeded, session_factory):
    with session_factory() as s:
        out = run(s, FailingProvider())
    assert out.used_fallback is True and out.provider == "keyword-heuristic"
    assert out.fallback_reason == "timeout"


def test_parse_json_tolerates_code_fences():
    assert ai._parse_json_object('```json\n{"suggestions": []}\n```') == {"suggestions": []}
    with pytest.raises(ai.ProviderError):
        ai._parse_json_object("no json here")


def test_openai_provider_request_and_parsing(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, body=json, headers=headers)
        content = '{"suggestions": [{"predecessor_id": 1, "successor_id": 2, "confidence": 0.9, "reason": "r r", "evidence": "e e e"}]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    provider = ai.OpenAIProvider("sk-test", "gpt-4o-mini", 5)
    out = provider.propose([], [])
    assert out[0]["successor_id"] == 2
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_provider_http_error_becomes_provider_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(ai.httpx, "post", boom)
    with pytest.raises(ai.ProviderError):
        ai.AnthropicProvider("key", "", 1).propose([], [])


def test_rate_limit():
    limiter = ai._RateLimiter()
    limiter.check(2)
    limiter.check(2)
    with pytest.raises(ai.ServiceError) as exc:
        limiter.check(2)
    assert exc.value.status == 429


def test_needs_two_tasks(client):
    r = client.post("/api/ai/suggestions")
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_enough_tasks"


def test_board_titles_used_in_prompt(seeded, session_factory):
    from app.services.board import load

    with session_factory() as s:
        snap = load(s)
        prompt = ai.build_user_prompt(sorted(snap.tasks.values(), key=lambda x: x.id), sorted(snap.graph.edges()))
    assert "Production release" in prompt and "Existing dependencies:" in prompt
    assert by_title(seeded.get("/api/board").json())
