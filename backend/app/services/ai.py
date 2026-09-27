"""AI-assisted dependency suggestions.

Flow
----
1. A provider proposes links from task titles and descriptions:
   * ``OpenAIProvider`` / ``AnthropicProvider`` call a hosted LLM,
   * ``KeywordProvider`` is a deterministic fallback that needs no network.
2. Every proposal goes through five gates (``_gate``) before anyone sees it.
3. Survivors are stored as *pending*. Nothing becomes a real dependency until
   a person accepts it, and acceptance goes through the same
   ``board.add_dependency`` path as a manual link, so the engine still has the
   final word on correctness.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import engine as eng
from ..config import Settings, get_settings
from ..models import AISuggestion, Task
from ..schemas import (
    AIMetricsOut,
    MutationResult,
    RawSuggestion,
    RawSuggestionList,
    SuggestionOut,
    SuggestRunOut,
)
from . import board as board_service
from .board import ServiceError

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You help plan software projects on a Kanban board.
A dependency "A -> B" means task B cannot start until task A is finished (finish-to-start).

Only suggest a dependency when the task text gives a concrete reason, for example
"after the schema is ready", "needs the API", "once staging is up".
Do not suggest links just because two tasks are in the same area.
Never invent task ids. Only use ids from the list you are given.
Do not repeat dependencies that already exist.

For every suggestion, copy a short phrase WORD FOR WORD from the successor's or the
predecessor's title or description as "evidence". If you cannot quote such a phrase,
do not make the suggestion.

Reply with JSON only, no prose, in exactly this shape:
{"suggestions": [{"predecessor_id": 1, "successor_id": 2, "confidence": 0.8,
  "reason": "one short sentence", "evidence": "exact phrase from the task text"}]}
Return {"suggestions": []} if nothing is clearly implied."""

FEW_SHOT = """Example
Tasks:
[1] Design login page: Mockups for the sign-in screen.
[2] Implement login page: Build the sign-in screen once the design is approved.
Existing dependencies: none
Answer:
{"suggestions": [{"predecessor_id": 1, "successor_id": 2, "confidence": 0.9,
  "reason": "Implementation waits for the approved design.",
  "evidence": "once the design is approved"}]}"""


def build_user_prompt(tasks: list[Task], edges: list[tuple[int, int]]) -> str:
    lines = [f"[{t.id}] {t.title}: {t.description.strip() or '(no description)'}" for t in tasks]
    existing = ", ".join(f"{a} -> {b}" for a, b in edges) or "none"
    return f"{FEW_SHOT}\n\nNow the real project.\nTasks:\n" + "\n".join(lines) + f"\nExisting dependencies: {existing}\nAnswer:"


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    model: str

    def propose(self, tasks: list[Task], edges: list[tuple[int, int]]) -> list[dict]: ...


def _parse_json_object(text: str) -> dict:
    """Extract the JSON object from a model reply, tolerating code fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("Model reply did not contain JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Model reply was not valid JSON: {exc.msg}") from exc


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float):
        self.api_key, self.model, self.timeout = api_key, model or "gpt-4o-mini", timeout

    def propose(self, tasks, edges):
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(tasks, edges)},
                    ],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"OpenAI request failed: {type(exc).__name__}") from exc
        return _parse_json_object(content).get("suggestions", [])


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float):
        self.api_key, self.model, self.timeout = api_key, model or "claude-haiku-4-5", timeout

    def propose(self, tasks, edges):
        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": self.model,
                    "max_tokens": 1500,
                    "temperature": 0,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": build_user_prompt(tasks, edges)}],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = "".join(block.get("text", "") for block in resp.json()["content"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"Anthropic request failed: {type(exc).__name__}") from exc
        return _parse_json_object(content).get("suggestions", [])


class KeywordProvider:
    """Deterministic fallback: looks for ordering cues that mention another task.

    For every task B it scans B's description for sentences that contain an
    ordering word (after, once, requires, ...) and enough of another task A's
    title keywords. That sentence becomes the evidence for A -> B.
    """

    name = "keyword-heuristic"
    model = "rules-v1"

    CUES = re.compile(
        r"\b(after|once|requires?|required|depends? on|dependent on|needs?|blocked by|"
        r"waits? for|following|prerequisites?|when .* (?:is|are) (?:done|ready|complete))\b",
        re.IGNORECASE,
    )
    STOPWORDS = {
        "the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "with", "set", "up",
        "build", "write", "run", "do", "task", "is", "are", "be", "from", "into", "all",
    }

    def propose(self, tasks, edges):
        suggestions = []
        for b in tasks:
            sentences = re.split(r"(?<=[.!?])\s+", b.description or "")
            for sentence in sentences:
                if not self.CUES.search(sentence):
                    continue
                words = set(self._words(sentence))
                for a in tasks:
                    if a.id == b.id:
                        continue
                    keys = self._keywords(a.title)
                    if not keys:
                        continue
                    hits = len(keys & words)
                    if hits and hits / len(keys) >= 0.5:
                        suggestions.append({
                            "predecessor_id": a.id,
                            "successor_id": b.id,
                            "confidence": round(0.45 + 0.4 * hits / len(keys), 2),
                            "reason": f"'{b.title}' mentions '{a.title}' with an ordering cue.",
                            "evidence": sentence.strip(),
                        })
        return suggestions

    @classmethod
    def _words(cls, text: str) -> list[str]:
        return [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower())]

    @classmethod
    def _keywords(cls, title: str) -> set[str]:
        return {w for w in cls._words(title) if w not in cls.STOPWORDS and len(w) > 2}


def pick_provider(settings: Settings) -> tuple[Provider, str | None]:
    """Choose the configured LLM, or the keyword fallback with the reason why."""
    want = settings.ai_provider
    if want in ("auto", "openai") and settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key, settings.ai_model, settings.ai_timeout_seconds), None
    if want in ("auto", "anthropic") and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.ai_model, settings.ai_timeout_seconds), None
    if want == "heuristic":
        return KeywordProvider(), None
    return KeywordProvider(), "No LLM API key configured"


# ---------------------------------------------------------------------------
# Rate limiting (per process; enough for a single-instance deployment)
# ---------------------------------------------------------------------------


class _RateLimiter:
    def __init__(self):
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, limit_per_minute: int) -> None:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= limit_per_minute:
                raise ServiceError(429, "rate_limited", "Too many AI requests. Please wait a minute and try again.")
            self._calls.append(now)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# Validation gates
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _gate(
    raw_items: list[dict],
    tasks: dict[int, Task],
    graph: eng.DependencyGraph,
    already_pending: set[tuple[int, int]],
) -> tuple[list[RawSuggestion], Counter]:
    """Apply the five gates. Returns the survivors and a count of drops per gate."""
    dropped: Counter = Counter()
    survivors: list[RawSuggestion] = []
    working = graph.copy()  # survivors are added here so they cannot form a loop together
    seen: set[tuple[int, int]] = set()

    for item in raw_items:
        # Gate 1: must match the schema.
        try:
            s = RawSuggestion.model_validate(item)
        except ValidationError:
            dropped["bad_format"] += 1
            continue
        pair = (s.predecessor_id, s.successor_id)
        # Gate 2: both tasks must exist.
        if s.predecessor_id not in tasks or s.successor_id not in tasks:
            dropped["unknown_task"] += 1
            continue
        # Gate 3: no self links, duplicates, existing or already-pending links.
        if s.predecessor_id == s.successor_id or pair in seen or graph.has_edge(*pair) or pair in already_pending:
            dropped["duplicate_or_self"] += 1
            continue
        # Gate 4: evidence must really appear in one of the two tasks' text.
        pred, succ = tasks[s.predecessor_id], tasks[s.successor_id]
        haystack = _normalise(" ".join([pred.title, pred.description, succ.title, succ.description]))
        needle = _normalise(s.evidence)
        if len(needle) < 3 or needle not in haystack:
            dropped["evidence_not_found"] += 1
            continue
        # Gate 5: engine dry run - must not create a cycle.
        try:
            eng.validate_new_edge(working, *pair)
        except eng.DependencyError:
            dropped["would_create_cycle"] += 1
            continue
        working.add_edge(*pair)
        seen.add(pair)
        survivors.append(s)
    return survivors, dropped


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def suggest(session: Session, settings: Settings | None = None, provider: Provider | None = None) -> SuggestRunOut:
    settings = settings or get_settings()
    snap = board_service.load(session)
    if len(snap.tasks) < 2:
        raise ServiceError(422, "not_enough_tasks", "Add at least two tasks before asking for suggestions.")

    fallback_reason = None
    if provider is None:
        rate_limiter.check(settings.ai_rate_limit_per_minute)
        provider, fallback_reason = pick_provider(settings)

    tasks_sorted = sorted(snap.tasks.values(), key=lambda t: t.id)
    edges = sorted(snap.graph.edges())
    try:
        raw = provider.propose(tasks_sorted, edges)
    except ProviderError as exc:
        # The LLM failed or timed out: degrade gracefully to the heuristic.
        fallback_reason = str(exc)
        provider = KeywordProvider()
        raw = provider.propose(tasks_sorted, edges)
    if not isinstance(raw, list):
        raw = []

    pending_pairs = {
        (s.predecessor_id, s.successor_id)
        for s in session.scalars(select(AISuggestion).where(AISuggestion.status == "pending"))
    }
    survivors, dropped = _gate(raw, snap.tasks, snap.graph, pending_pairs)

    for s in survivors:
        session.add(AISuggestion(
            predecessor_id=s.predecessor_id,
            successor_id=s.successor_id,
            confidence=s.confidence,
            rationale=s.reason,
            evidence=s.evidence,
            model=f"{provider.name}:{provider.model}",
        ))
    session.commit()

    return SuggestRunOut(
        provider=provider.name,
        model=provider.model,
        used_fallback=isinstance(provider, KeywordProvider) and fallback_reason is not None,
        fallback_reason=fallback_reason,
        proposed=len(raw),
        shown=len(survivors),
        dropped=dict(dropped),
        suggestions=list_suggestions(session, status="pending"),
    )


def list_suggestions(session: Session, status: str | None = "pending") -> list[SuggestionOut]:
    query = select(AISuggestion).order_by(AISuggestion.confidence.desc(), AISuggestion.id)
    if status:
        query = query.where(AISuggestion.status == status)
    titles = dict(session.execute(select(Task.id, Task.title)).all())
    return [
        SuggestionOut(
            id=s.id,
            predecessor_id=s.predecessor_id,
            successor_id=s.successor_id,
            predecessor_title=titles.get(s.predecessor_id, "?"),
            successor_title=titles.get(s.successor_id, "?"),
            confidence=s.confidence,
            rationale=s.rationale,
            evidence=s.evidence,
            model=s.model,
            status=s.status,
        )
        for s in session.scalars(query)
    ]


def accept(session: Session, suggestion_id: int) -> MutationResult:
    suggestion = _pending(session, suggestion_id)
    try:
        # Same path as a manual link: the engine re-validates against the
        # graph as it is now, which may have changed since the suggestion.
        result = board_service.add_dependency(
            session, suggestion.predecessor_id, suggestion.successor_id, source="ai"
        )
    except ServiceError:
        session.rollback()
        suggestion = session.get(AISuggestion, suggestion_id)
        suggestion.status = "stale"
        suggestion.decided_at = datetime.now(timezone.utc)
        session.commit()
        raise
    suggestion = session.get(AISuggestion, suggestion_id)
    suggestion.status = "accepted"
    suggestion.decided_at = datetime.now(timezone.utc)
    session.commit()
    return result


def reject(session: Session, suggestion_id: int) -> None:
    suggestion = _pending(session, suggestion_id)
    suggestion.status = "rejected"
    suggestion.decided_at = datetime.now(timezone.utc)
    session.commit()


def metrics(session: Session) -> AIMetricsOut:
    counts = dict(session.execute(select(AISuggestion.status, func.count()).group_by(AISuggestion.status)).all())
    accepted, rejected, pending = counts.get("accepted", 0), counts.get("rejected", 0), counts.get("pending", 0)
    decided = accepted + rejected
    return AIMetricsOut(
        shown=sum(counts.values()),
        accepted=accepted,
        rejected=rejected,
        pending=pending,
        precision=round(accepted / decided, 3) if decided else None,
    )


def _pending(session: Session, suggestion_id: int) -> AISuggestion:
    suggestion = session.get(AISuggestion, suggestion_id)
    if suggestion is None:
        raise ServiceError(404, "not_found", "Suggestion not found")
    if suggestion.status != "pending":
        raise ServiceError(409, "already_decided", f"This suggestion was already {suggestion.status}.")
    return suggestion
