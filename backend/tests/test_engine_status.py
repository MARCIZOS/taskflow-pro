from app.engine import (
    DependencyGraph,
    Status,
    check_status_change,
    newly_blocked_by_regression,
    readiness,
)

B, P, R, D = Status.BACKLOG, Status.IN_PROGRESS, Status.REVIEW, Status.DONE


def schema_api_tests():
    # Database Schema -> Backend API -> Integration Tests
    return DependencyGraph.build(["schema", "api", "tests"], [("schema", "api"), ("api", "tests")])


def test_task_without_prerequisites_is_ready():
    g = schema_api_tests()
    assert readiness(g, {"schema": B, "api": B, "tests": B})["schema"] == []


def test_task_is_blocked_until_all_prerequisites_are_done():
    g = DependencyGraph.build("ABC", [("A", "C"), ("B", "C")])
    assert readiness(g, {"A": D, "B": R, "C": B})["C"] == ["B"]
    assert readiness(g, {"A": D, "B": D, "C": B})["C"] == []


def test_blocked_task_cannot_start():
    g = schema_api_tests()
    statuses = {"schema": P, "api": B, "tests": B}
    assert check_status_change(g, statuses, "api", P) == ["schema"]
    assert check_status_change(g, statuses, "api", D) == ["schema"]


def test_backlog_is_always_allowed():
    g = schema_api_tests()
    assert check_status_change(g, {"schema": P, "api": P, "tests": B}, "api", B) == []


def test_rollback_on_regression_reblocks_downstream():
    g = schema_api_tests()
    before = {"schema": D, "api": D, "tests": P}
    after = {**before, "api": P}  # API moved back from Done to In Progress
    assert newly_blocked_by_regression(g, before, after) == ["tests"]
    assert readiness(g, after)["tests"] == ["api"]


def test_regression_only_affects_direct_dependents_readiness():
    g = schema_api_tests()
    before = {"schema": D, "api": D, "tests": B}
    after = {**before, "schema": P}
    # api loses a finished prerequisite; tests still has api Done so stays ready.
    assert newly_blocked_by_regression(g, before, after) == ["api"]
