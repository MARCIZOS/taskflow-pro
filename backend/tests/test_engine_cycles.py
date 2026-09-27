import pytest

from app.engine import (
    CycleError,
    DependencyGraph,
    DuplicateDependencyError,
    SelfDependencyError,
    UnknownTaskError,
    find_path,
    topological_order,
    validate_new_edge,
)


def chain(*ids):
    return DependencyGraph.build(ids, list(zip(ids, ids[1:])))


def test_self_dependency_rejected():
    g = chain("A")
    with pytest.raises(SelfDependencyError):
        validate_new_edge(g, "A", "A")


def test_duplicate_dependency_rejected():
    g = chain("A", "B")
    with pytest.raises(DuplicateDependencyError):
        validate_new_edge(g, "A", "B")


def test_unknown_task_rejected():
    g = chain("A", "B")
    with pytest.raises(UnknownTaskError):
        validate_new_edge(g, "A", "Z")


def test_two_node_cycle_rejected_with_path():
    g = chain("A", "B")
    with pytest.raises(CycleError) as exc:
        validate_new_edge(g, "B", "A")
    assert exc.value.cycle == ["B", "A", "B"]


def test_three_node_cycle_from_problem_statement():
    # A -> B -> C exists; adding C -> A would give A -> B -> C -> A.
    g = chain("A", "B", "C")
    with pytest.raises(CycleError) as exc:
        validate_new_edge(g, "C", "A")
    assert exc.value.cycle == ["C", "A", "B", "C"]
    assert "C -> A -> B -> C" in str(exc.value)


def test_validation_is_a_dry_run_and_leaves_graph_unchanged():
    g = chain("A", "B", "C")
    before = sorted(g.edges())
    with pytest.raises(CycleError):
        validate_new_edge(g, "C", "A")
    assert sorted(g.edges()) == before


def test_long_cycle_through_deep_chain():
    ids = [f"T{i}" for i in range(2000)]  # deeper than Python's recursion limit
    g = chain(*ids)
    with pytest.raises(CycleError) as exc:
        validate_new_edge(g, "T1999", "T0")
    assert exc.value.cycle[0] == "T1999" and exc.value.cycle[-1] == "T1999"
    assert len(exc.value.cycle) == 2001


def test_diamond_is_not_a_cycle():
    g = DependencyGraph.build("ABCD", [("A", "B"), ("A", "C"), ("B", "D")])
    validate_new_edge(g, "C", "D")  # converging paths are fine


def test_shortcut_edge_is_allowed():
    g = chain("A", "B", "C")
    validate_new_edge(g, "A", "C")  # redundant but acyclic


def test_find_path():
    g = chain("A", "B", "C")
    assert find_path(g, "A", "C") == ["A", "B", "C"]
    assert find_path(g, "C", "A") is None


def test_topological_order_respects_edges_and_is_deterministic():
    g = DependencyGraph.build("ABCDE", [("A", "C"), ("B", "C"), ("C", "D")])
    order = topological_order(g)
    assert order == ["A", "B", "E", "C", "D"]
    for a, b in g.edges():
        assert order.index(a) < order.index(b)
    assert order == topological_order(g)
