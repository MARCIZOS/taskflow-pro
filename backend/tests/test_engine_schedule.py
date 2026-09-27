from datetime import date

from app.engine import (
    DependencyGraph,
    TaskDates,
    constraint_violations,
    critical_path,
    propagate_schedule,
)


def d(month, day):
    return date(2026, month, day)


def span(start, end):
    return TaskDates(start, end)


def diamond():
    """A -> B -> D and A -> C -> D with no slack, as in the problem statement."""
    g = DependencyGraph.build("ABCD", [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    dates = {
        "A": span(d(10, 1), d(10, 3)),
        "B": span(d(10, 4), d(10, 6)),
        "C": span(d(10, 4), d(10, 6)),
        "D": span(d(10, 7), d(10, 9)),
    }
    return g, dates


def test_diamond_does_not_compound():
    g, dates = diamond()
    dates["A"] = span(d(10, 1), d(10, 6))  # A extended by 3 days
    changes = propagate_schedule(g, dates, ["A"])
    assert changes["B"].shift_days == 3
    assert changes["C"].shift_days == 3
    assert changes["D"].shift_days == 3  # not 6
    assert changes["D"].new == span(d(10, 10), d(10, 12))


def test_uneven_branch_moves_downstream_only_by_real_impact():
    # C is shorter, so it has 2 days of slack before D. B is tight.
    g = DependencyGraph.build("ABCD", [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    dates = {
        "A": span(d(10, 1), d(10, 3)),
        "B": span(d(10, 4), d(10, 4)),  # 1 day, ends Oct 4, 2 days slack before D
        "C": span(d(10, 4), d(10, 6)),  # 3 days, tight
        "D": span(d(10, 7), d(10, 9)),
    }
    # Delay only B by 3 days: B now ends Oct 7, so D must start Oct 8 (+1, not +3).
    dates["B"] = span(d(10, 7), d(10, 7))
    changes = propagate_schedule(g, dates, ["B"])
    assert changes["D"].shift_days == 1


def test_many_converging_paths_move_once():
    # A fans out to 10 middle tasks, all of which feed Z.
    mids = [f"M{i}" for i in range(10)]
    edges = [("A", m) for m in mids] + [(m, "Z") for m in mids]
    g = DependencyGraph.build(["A", "Z", *mids], edges)
    dates = {"A": span(d(10, 1), d(10, 1)), "Z": span(d(10, 3), d(10, 3))}
    dates.update({m: span(d(10, 2), d(10, 2)) for m in mids})
    dates["A"] = span(d(10, 1), d(10, 5))  # +4 days
    changes = propagate_schedule(g, dates, ["A"])
    assert changes["Z"].shift_days == 4


def test_multi_level_propagation_preserves_durations():
    ids = list("ABCDE")
    g = DependencyGraph.build(ids, list(zip(ids, ids[1:])))
    dates = {}
    cursor = d(10, 1)
    for i, t in enumerate(ids):
        length = i + 1
        dates[t] = span(cursor, date.fromordinal(cursor.toordinal() + length - 1))
        cursor = date.fromordinal(dates[t].end.toordinal() + 1)
    dates["A"] = dates["A"].shifted(2)
    changes = propagate_schedule(g, dates, ["A"])
    assert [changes[t].shift_days for t in "BCDE"] == [2, 2, 2, 2]
    for t in "BCDE":
        assert changes[t].new.duration_days == dates[t].duration_days


def test_no_changes_when_there_is_enough_slack():
    g = DependencyGraph.build("AB", [("A", "B")])
    dates = {"A": span(d(10, 1), d(10, 2)), "B": span(d(10, 10), d(10, 11))}
    dates["A"] = span(d(10, 1), d(10, 5))
    assert propagate_schedule(g, dates, ["A"]) == {}


def test_tasks_are_never_pulled_earlier():
    g, dates = diamond()
    dates["A"] = span(d(9, 28), d(9, 30))  # A finishes 3 days early
    assert propagate_schedule(g, dates, ["A"]) == {}


def test_unrelated_tasks_are_untouched():
    g = DependencyGraph.build("ABXY", [("A", "B"), ("X", "Y")])
    dates = {
        "A": span(d(10, 1), d(10, 1)),
        "B": span(d(10, 2), d(10, 2)),
        "X": span(d(10, 1), d(10, 1)),
        "Y": span(d(10, 2), d(10, 2)),
    }
    dates["A"] = span(d(10, 1), d(10, 3))
    assert set(propagate_schedule(g, dates, ["A"])) == {"B"}


def test_propagation_does_not_mutate_input():
    g, dates = diamond()
    dates["A"] = span(d(10, 1), d(10, 6))
    snapshot = dict(dates)
    propagate_schedule(g, dates, ["A"])
    assert dates == snapshot


def test_constraint_violations():
    g = DependencyGraph.build("AB", [("A", "B")])
    bad = {"A": span(d(10, 1), d(10, 5)), "B": span(d(10, 3), d(10, 4))}
    assert constraint_violations(g, bad) == [("A", "B")]


def test_critical_path_on_diamond_with_slack():
    g = DependencyGraph.build("ABCD", [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    dates = {
        "A": span(d(10, 1), d(10, 3)),
        "B": span(d(10, 4), d(10, 6)),
        "C": span(d(10, 4), d(10, 4)),
        "D": span(d(10, 7), d(10, 9)),
    }
    result = critical_path(g, dates)
    assert result.slack_days == {"A": 0, "B": 0, "C": 2, "D": 0}
    assert result.critical_chain == ["A", "B", "D"]
    assert result.project_end == d(10, 9)


def test_critical_path_empty_graph():
    result = critical_path(DependencyGraph(), {})
    assert result.critical_chain == []
