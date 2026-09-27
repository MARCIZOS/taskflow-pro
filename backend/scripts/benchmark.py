"""Time the engine on a large random project.

    python -m scripts.benchmark            # 1,000 tasks, 3,000 links
    python -m scripts.benchmark 5000 15000
"""

from __future__ import annotations

import random
import sys
import time
from datetime import date, timedelta
from statistics import median

from app.engine import (
    CycleError,
    DependencyGraph,
    TaskDates,
    critical_path,
    propagate_schedule,
    topological_order,
    validate_new_edge,
)


def build(n: int, m: int, seed: int = 7):
    rng = random.Random(seed)
    edges: set[tuple[int, int]] = set()
    while len(edges) < m:
        a, b = sorted(rng.sample(range(n), 2))
        edges.add((a, b))
    g = DependencyGraph.build(range(n), edges)
    dates: dict[int, TaskDates] = {}
    start = date(2026, 10, 1)
    for t in topological_order(g):
        ends = [dates[p].end for p in g.pred.get(t, ())]
        s = max(ends) + timedelta(days=1) if ends else start
        dates[t] = TaskDates(s, s + timedelta(days=rng.randint(0, 4)))
    return g, dates


def timed(fn, repeat: int = 20) -> float:
    runs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000)
    return median(runs)


def main(n: int = 1000, m: int = 3000) -> None:
    g, dates = build(n, m)
    root = topological_order(g)[0]
    delayed = dict(dates)
    delayed[root] = TaskDates(dates[root].start, dates[root].end + timedelta(days=3))
    last = topological_order(g)[-1]

    def cycle_check():
        try:
            validate_new_edge(g, last, root)
        except CycleError:
            pass

    print(f"Graph: {n} tasks, {m} links")
    print(f"  cycle check (worst case)     {timed(cycle_check):7.2f} ms")
    print(f"  propagate +3 days from root  {timed(lambda: propagate_schedule(g, delayed, [root])):7.2f} ms")
    print(f"  critical path                {timed(lambda: critical_path(g, dates)):7.2f} ms")


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    main(*args)
