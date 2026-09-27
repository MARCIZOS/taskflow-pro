"""Property-based tests: random DAGs, random delays, invariants must always hold."""

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine import (
    CycleError,
    DependencyGraph,
    TaskDates,
    constraint_violations,
    descendants,
    find_path,
    propagate_schedule,
    topological_order,
    validate_new_edge,
)

BASE = date(2026, 10, 1)


@st.composite
def dag_with_schedule(draw):
    n = draw(st.integers(min_value=2, max_value=25))
    nodes = list(range(n))
    # Only allow edges i -> j with i < j, which guarantees a DAG.
    pairs = [(i, j) for i in nodes for j in nodes if i < j]
    edges = draw(st.lists(st.sampled_from(pairs), unique=True, max_size=min(60, len(pairs))))
    g = DependencyGraph.build(nodes, edges)
    durations = draw(st.lists(st.integers(1, 5), min_size=n, max_size=n))
    # Build a valid schedule: each task starts after all prerequisites end,
    # plus some random slack.
    dates = {}
    for t in topological_order(g):
        slack = draw(st.integers(0, 3))
        ends = [dates[p].end for p in g.pred.get(t, ())]
        start = (max(ends) + timedelta(days=1) if ends else BASE) + timedelta(days=slack)
        dates[t] = TaskDates(start, start + timedelta(days=durations[t] - 1))
    return g, dates


@settings(max_examples=300, deadline=None)
@given(data=dag_with_schedule(), pick=st.integers(0, 10_000), delay=st.integers(1, 10), extend=st.booleans())
def test_propagation_invariants(data, pick, delay, extend):
    g, dates = data
    assert constraint_violations(g, dates) == []
    task = sorted(g.nodes)[pick % len(g.nodes)]
    updated = dict(dates)
    old = dates[task]
    updated[task] = TaskDates(old.start, old.end + timedelta(days=delay)) if extend else old.shifted(delay)

    changes = propagate_schedule(g, updated, [task])
    final = {**updated, **{t: c.new for t, c in changes.items()}}

    # 1. Every dependency is respected after propagation.
    assert constraint_violations(g, final) == []
    # 2. No compounding: nothing moves further than the original delay.
    assert all(0 < c.shift_days <= delay for c in changes.values())
    # 3. Only descendants of the changed task can move.
    assert set(changes) <= descendants(g, [task])
    # 4. Durations are preserved.
    assert all(c.new.duration_days == c.old.duration_days for c in changes.values())
    # 5. Idempotent: running it again changes nothing.
    assert propagate_schedule(g, final, [task]) == {}
    # 6. Minimal: every moved task starts exactly the day after a prerequisite ends.
    for t, c in changes.items():
        assert c.new.start == max(final[p].end for p in g.pred[t]) + timedelta(days=1)


@settings(max_examples=300, deadline=None)
@given(data=dag_with_schedule(), a=st.integers(0, 10_000), b=st.integers(0, 10_000))
def test_cycle_check_matches_reachability(data, a, b):
    g, _ = data
    ordered = sorted(g.nodes)
    pred, succ = ordered[a % len(ordered)], ordered[b % len(ordered)]
    if pred == succ or g.has_edge(pred, succ):
        return
    creates_cycle = find_path(g, succ, pred) is not None
    try:
        validate_new_edge(g, pred, succ)
        accepted = True
    except CycleError as exc:
        accepted = False
        cycle = exc.cycle
        # The reported loop is real: consecutive tasks are linked, and it closes.
        assert cycle[0] == cycle[-1] == pred
        links = list(zip(cycle, cycle[1:]))
        assert links[0] == (pred, succ)
        assert all(g.has_edge(x, y) for x, y in links[1:])
    assert accepted is (not creates_cycle)
    if accepted:
        g.add_edge(pred, succ)
        topological_order(g)  # still a DAG
