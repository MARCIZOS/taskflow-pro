"""End-to-end API tests against the seeded demo board."""

from tests.conftest import by_title


def board(client):
    return client.get("/api/board").json()


def move(client, task, status, index=0):
    return client.post(f"/api/tasks/{task['id']}/move", json={"version": task["version"], "status": status, "index": index})


def test_seed_has_ten_tasks_and_valid_readiness(seeded):
    b = board(seeded)
    assert len(b["tasks"]) == 10
    t = by_title(b)
    assert t["Build backend API"]["is_blocked"] is True
    assert set(t["Build backend API"]["blocked_by"]) == {t["Design database schema"]["id"], t["Set up authentication service"]["id"]}
    assert t["Build frontend UI"]["is_blocked"] is False


def test_cycle_is_rejected_and_nothing_is_saved(seeded):
    t = by_title(board(seeded))
    before = board(seeded)["dependencies"]
    r = seeded.post("/api/dependencies", json={
        "predecessor_id": t["Production release"]["id"], "successor_id": t["Gather requirements"]["id"]})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "cycle"
    assert err["cycle"][0] == err["cycle"][-1] == t["Production release"]["id"]
    # BFS reports the shortest loop:
    # release -> requirements -> frontend UI -> integration tests -> staging -> UAT -> release
    names = {v["id"]: k for k, v in t.items()}
    assert [names[i] for i in err["cycle"][1:-1]] == [
        "Gather requirements", "Build frontend UI", "Write integration tests", "Deploy to staging",
        "User acceptance testing",
    ]
    assert "loop" in err["message"]
    assert board(seeded)["dependencies"] == before


def test_validate_endpoint_is_a_dry_run(seeded):
    t = by_title(board(seeded))
    body = {"predecessor_id": t["Build backend API"]["id"], "successor_id": t["Design database schema"]["id"]}
    r = seeded.post("/api/dependencies/validate", json=body)
    assert r.json()["valid"] is False and r.json()["code"] == "cycle"
    ok = seeded.post("/api/dependencies/validate", json={
        "predecessor_id": t["Security review"]["id"], "successor_id": t["Production release"]["id"]})
    assert ok.json() == {"valid": True, "code": None, "message": None, "cycle": None}


def test_self_and_duplicate_dependencies(seeded):
    t = by_title(board(seeded))
    api, schema = t["Build backend API"]["id"], t["Design database schema"]["id"]
    assert seeded.post("/api/dependencies", json={"predecessor_id": api, "successor_id": api}).status_code == 409
    dup = seeded.post("/api/dependencies", json={"predecessor_id": schema, "successor_id": api})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_dependency"


def test_diamond_extension_moves_downstream_once(seeded):
    t = by_title(board(seeded))
    req = t["Gather requirements"]
    api_before = t["Build backend API"]
    # Extend requirements by 3 days (Oct 3 -> Oct 6).
    r = seeded.patch(f"/api/tasks/{req['id']}", json={"version": req["version"], "end_date": "2026-10-06"})
    assert r.status_code == 200, r.text
    shifts = {c["title"]: c["shift_days"] for c in r.json()["schedule_changes"]}
    assert shifts["Design database schema"] == 3
    assert shifts["Set up authentication service"] == 3
    assert shifts["Build backend API"] == 3  # not 6
    api_after = by_title(r.json()["board"])["Build backend API"]
    assert api_after["start_date"] == "2026-10-10"
    assert api_after["duration_days"] == api_before["duration_days"]
    # Downstream of the API moves too, and the whole plan stays valid.
    assert shifts["Production release"] == 3


def test_cannot_start_before_prerequisite_finishes(seeded):
    t = by_title(board(seeded))
    api = t["Build backend API"]
    r = seeded.patch(f"/api/tasks/{api['id']}", json={
        "version": api["version"], "start_date": "2026-10-02", "end_date": "2026-10-06"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "starts_before_prerequisite"
    assert r.json()["error"]["earliest_start"] == "2026-10-07"


def test_blocked_task_cannot_be_started(seeded):
    t = by_title(board(seeded))
    r = move(seeded, t["Build backend API"], "in_progress")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "blocked"
    assert by_title(board(seeded))["Build backend API"]["status"] == "backlog"


def test_ready_task_can_be_started_and_finished(seeded):
    t = by_title(board(seeded))
    r = move(seeded, t["Build frontend UI"], "in_progress")
    assert r.status_code == 200
    ui = by_title(r.json()["board"])["Build frontend UI"]
    assert ui["status"] == "in_progress"


def test_rollback_on_regression(seeded):
    t = by_title(board(seeded))
    # Finish schema and auth so the API becomes ready, then start the API.
    for name in ("Design database schema", "Set up authentication service"):
        task = by_title(board(seeded))[name]
        assert move(seeded, task, "done").status_code == 200
    assert by_title(board(seeded))["Build backend API"]["is_blocked"] is False
    assert move(seeded, by_title(board(seeded))["Build backend API"], "in_progress").status_code == 200

    # Now move schema back from Done to In Progress.
    r = move(seeded, by_title(board(seeded))["Design database schema"], "in_progress")
    assert r.status_code == 200
    api = by_title(r.json()["board"])["Build backend API"]
    assert r.json()["newly_blocked"] == [t["Build backend API"]["id"]]
    assert api["is_blocked"] is True and api["status"] == "in_progress"
    assert "blocked again" in r.json()["message"]


def test_stale_version_is_rejected(seeded):
    ui = by_title(board(seeded))["Build frontend UI"]
    assert move(seeded, ui, "backlog", 0).status_code == 200
    r = move(seeded, ui, "in_progress")  # still using the old version
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_version"


def test_reordering_within_a_column_persists(seeded):
    col = [x for x in board(seeded)["tasks"] if x["status"] == "backlog"]
    last = col[-1]
    assert move(seeded, last, "backlog", 0).status_code == 200
    new_col = [x for x in board(seeded)["tasks"] if x["status"] == "backlog"]
    assert new_col[0]["id"] == last["id"]


def test_adding_a_link_pushes_the_successor(seeded):
    created = seeded.post("/api/tasks", json={
        "title": "Write release notes", "description": "", "start_date": "2026-10-01", "end_date": "2026-10-02"})
    assert created.status_code == 201
    notes = by_title(created.json()["board"])["Write release notes"]
    uat = by_title(board(seeded))["User acceptance testing"]
    r = seeded.post("/api/dependencies", json={"predecessor_id": uat["id"], "successor_id": notes["id"]})
    assert r.status_code == 201
    moved = by_title(r.json()["board"])["Write release notes"]
    assert moved["start_date"] == "2026-10-20"  # day after UAT ends
    assert moved["duration_days"] == 2


def test_delete_task_unblocks_dependents(seeded):
    t = by_title(board(seeded))
    for name in ("Design database schema",):
        assert move(seeded, by_title(board(seeded))[name], "done").status_code == 200
    r = seeded.delete(f"/api/tasks/{t['Set up authentication service']['id']}")
    assert r.status_code == 200
    assert by_title(r.json()["board"])["Build backend API"]["is_blocked"] is False


def test_remove_dependency(seeded):
    dep = board(seeded)["dependencies"][0]
    assert seeded.delete(f"/api/dependencies/{dep['id']}").status_code == 200
    assert seeded.delete(f"/api/dependencies/{dep['id']}").status_code == 404


def test_input_validation(seeded):
    r = seeded.post("/api/tasks", json={"title": "", "start_date": "2026-10-01", "end_date": "2026-10-02"})
    assert r.status_code == 422
    r = seeded.post("/api/tasks", json={"title": "x", "start_date": "2026-10-05", "end_date": "2026-10-02"})
    assert r.status_code == 422 and "end_date" in r.json()["error"]["message"]
    assert seeded.get("/api/does-not-exist").status_code == 404
    assert seeded.post("/api/tasks/9999/move", json={"version": 1, "status": "done", "index": 0}).status_code == 404


def test_critical_path_on_seed(seeded):
    b = board(seeded)
    t = {x["id"]: x for x in b["tasks"]}
    titles = [t[i]["title"] for i in b["critical_chain"]]
    assert titles[0] == "Gather requirements"
    assert titles[-1] == "Production release"
    assert "Build backend API" in titles
    assert by_title(b)["Build frontend UI"]["slack_days"] > 0


def test_state_persists_across_requests(seeded):
    ui = by_title(board(seeded))["Build frontend UI"]
    move(seeded, ui, "review")
    # A fresh GET (like a browser refresh) sees the same state.
    assert by_title(board(seeded))["Build frontend UI"]["status"] == "review"


def test_demo_reset(seeded):
    ui = by_title(board(seeded))["Build frontend UI"]
    move(seeded, ui, "review")
    r = seeded.post("/api/demo/reset")
    assert r.status_code == 200
    assert by_title(r.json())["Build frontend UI"]["status"] == "backlog"
