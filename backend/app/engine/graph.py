"""Pure dependency engine for TaskFlow Pro.

This module has no database, web or framework code. It works on plain data
(task ids, dates, statuses and predecessor -> successor edges) so every rule
can be unit tested in isolation. The service layer loads data from the
database, calls these functions and persists whatever they return.

Conventions
-----------
* An edge ``(a, b)`` means "a must finish before b can start" (finish-to-start).
* Dates are calendar days and inclusive: a task from Oct 4 to Oct 6 lasts 3 days.
* A successor may start at the earliest one day after its latest predecessor ends.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Hashable, Iterable, Mapping

TaskId = Hashable
Edge = tuple[TaskId, TaskId]

ONE_DAY = timedelta(days=1)


class Status(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


# Columns a task may only enter once every prerequisite is Done.
GATED_STATUSES = frozenset({Status.IN_PROGRESS, Status.REVIEW, Status.DONE})


@dataclass(frozen=True)
class TaskDates:
    start: date
    end: date

    @property
    def duration_days(self) -> int:
        return (self.end - self.start).days + 1

    def shifted(self, days: int) -> "TaskDates":
        delta = timedelta(days=days)
        return TaskDates(self.start + delta, self.end + delta)


@dataclass
class DependencyGraph:
    """Adjacency-list view of the task graph.

    ``succ[a]`` holds the tasks that depend on ``a``; ``pred[b]`` holds the
    prerequisites of ``b``. Every known task id appears in ``nodes`` even when
    it has no edges.
    """

    nodes: set[TaskId] = field(default_factory=set)
    succ: dict[TaskId, set[TaskId]] = field(default_factory=lambda: defaultdict(set))
    pred: dict[TaskId, set[TaskId]] = field(default_factory=lambda: defaultdict(set))

    @classmethod
    def build(cls, nodes: Iterable[TaskId], edges: Iterable[Edge]) -> "DependencyGraph":
        graph = cls()
        for node in nodes:
            graph.nodes.add(node)
        for a, b in edges:
            graph.add_edge(a, b)
        return graph

    def add_edge(self, a: TaskId, b: TaskId) -> None:
        self.nodes.update((a, b))
        self.succ[a].add(b)
        self.pred[b].add(a)

    def remove_edge(self, a: TaskId, b: TaskId) -> None:
        self.succ[a].discard(b)
        self.pred[b].discard(a)

    def has_edge(self, a: TaskId, b: TaskId) -> bool:
        return b in self.succ.get(a, ())

    def edges(self) -> list[Edge]:
        return [(a, b) for a, targets in self.succ.items() for b in targets]

    def copy(self) -> "DependencyGraph":
        return DependencyGraph.build(self.nodes, self.edges())


# ---------------------------------------------------------------------------
# Rule 1: no cycles
# ---------------------------------------------------------------------------


class DependencyError(ValueError):
    """Raised when a proposed dependency is not allowed."""

    code = "invalid_dependency"


class SelfDependencyError(DependencyError):
    code = "self_dependency"


class DuplicateDependencyError(DependencyError):
    code = "duplicate_dependency"


class UnknownTaskError(DependencyError):
    code = "unknown_task"


class CycleError(DependencyError):
    code = "cycle"

    def __init__(self, cycle: list[TaskId]):
        self.cycle = cycle
        super().__init__("Adding this dependency would create a cycle: " + " -> ".join(map(str, cycle)))


def find_path(graph: DependencyGraph, source: TaskId, target: TaskId) -> list[TaskId] | None:
    """Return the shortest path from ``source`` to ``target``, or None.

    Breadth-first search, so a rejected link is explained with the smallest
    loop it would create. Iterative, so very deep chains cannot hit Python's
    recursion limit. Runs in O(V + E).
    """
    if source == target:
        return [source]
    parent: dict[TaskId, TaskId] = {}
    seen = {source}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for nxt in sorted(graph.succ.get(node, ()), key=_sort_key):
            if nxt in seen:
                continue
            parent[nxt] = node
            if nxt == target:
                path = [target]
                while path[-1] != source:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            seen.add(nxt)
            queue.append(nxt)
    return None


def validate_new_edge(graph: DependencyGraph, predecessor: TaskId, successor: TaskId) -> None:
    """Raise a ``DependencyError`` if ``predecessor -> successor`` is not allowed.

    The graph is never modified, which makes this safe to use as a dry run.
    """
    if predecessor not in graph.nodes or successor not in graph.nodes:
        raise UnknownTaskError("Both tasks must exist")
    if predecessor == successor:
        raise SelfDependencyError("A task cannot depend on itself")
    if graph.has_edge(predecessor, successor):
        raise DuplicateDependencyError("This dependency already exists")
    # The new edge closes a loop exactly when predecessor is already reachable
    # from successor. Report the loop in reading order, e.g. C -> A -> B -> C.
    back_path = find_path(graph, successor, predecessor)
    if back_path is not None:
        raise CycleError([predecessor] + back_path)


def topological_order(graph: DependencyGraph, subset: Iterable[TaskId] | None = None) -> list[TaskId]:
    """Kahn's algorithm, restricted to ``subset`` when given.

    Ties are broken by sorted id so results are deterministic. Raises
    ``CycleError`` if the (sub)graph is not a DAG, which should never happen
    for persisted data but is checked defensively.
    """
    nodes = set(graph.nodes if subset is None else subset)
    indegree = {n: 0 for n in nodes}
    for n in nodes:
        for p in graph.pred.get(n, ()):
            if p in nodes:
                indegree[n] += 1
    ready = sorted((n for n, d in indegree.items() if d == 0), key=_sort_key)
    queue = deque(ready)
    order: list[TaskId] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        released = []
        for nxt in graph.succ.get(node, ()):
            if nxt not in nodes:
                continue
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                released.append(nxt)
        queue.extend(sorted(released, key=_sort_key))
    if len(order) != len(nodes):
        leftover = [n for n in nodes if n not in set(order)]
        raise CycleError(sorted(leftover, key=_sort_key))
    return order


def descendants(graph: DependencyGraph, roots: Iterable[TaskId]) -> set[TaskId]:
    """All tasks reachable from ``roots`` by following one or more edges."""
    seen: set[TaskId] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        for nxt in graph.succ.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


# ---------------------------------------------------------------------------
# Readiness (Blocked / Ready) and rollback on regression
# ---------------------------------------------------------------------------


def blockers(graph: DependencyGraph, statuses: Mapping[TaskId, Status], task: TaskId) -> list[TaskId]:
    """Prerequisites of ``task`` that are not Done yet."""
    return sorted(
        (p for p in graph.pred.get(task, ()) if statuses.get(p) != Status.DONE),
        key=_sort_key,
    )


def readiness(graph: DependencyGraph, statuses: Mapping[TaskId, Status]) -> dict[TaskId, list[TaskId]]:
    """Map every task to its list of unfinished prerequisites.

    An empty list means Ready, anything else means Blocked. Readiness is always
    derived from current statuses, never stored, so a regression upstream is
    reflected downstream immediately (rollback on regression).
    """
    return {task: blockers(graph, statuses, task) for task in graph.nodes}


def check_status_change(
    graph: DependencyGraph, statuses: Mapping[TaskId, Status], task: TaskId, new_status: Status
) -> list[TaskId]:
    """Return the blockers that forbid moving ``task`` into ``new_status``.

    Moving into Backlog is always allowed. Moving into In Progress, Review or
    Done requires every prerequisite to be Done.
    """
    if new_status not in GATED_STATUSES:
        return []
    return blockers(graph, statuses, task)


def newly_blocked_by_regression(
    graph: DependencyGraph,
    before: Mapping[TaskId, Status],
    after: Mapping[TaskId, Status],
) -> list[TaskId]:
    """Tasks that were Ready before a status change and are Blocked after it."""
    was = readiness(graph, before)
    now = readiness(graph, after)
    return sorted((t for t in graph.nodes if not was[t] and now[t]), key=_sort_key)


# ---------------------------------------------------------------------------
# Rule 2: schedule propagation without compounding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleChange:
    task: TaskId
    old: TaskDates
    new: TaskDates

    @property
    def shift_days(self) -> int:
        return (self.new.start - self.old.start).days


def earliest_start(graph: DependencyGraph, dates: Mapping[TaskId, TaskDates], task: TaskId) -> date | None:
    """One day after the latest prerequisite end, or None without prerequisites."""
    ends = [dates[p].end for p in graph.pred.get(task, ()) if p in dates]
    return max(ends) + ONE_DAY if ends else None


def constraint_violations(graph: DependencyGraph, dates: Mapping[TaskId, TaskDates]) -> list[Edge]:
    """Edges whose successor starts before its predecessor has finished."""
    return sorted(
        ((a, b) for a, b in graph.edges() if dates[b].start <= dates[a].end),
        key=lambda e: (_sort_key(e[0]), _sort_key(e[1])),
    )


def propagate_schedule(
    graph: DependencyGraph,
    dates: Mapping[TaskId, TaskDates],
    changed: Iterable[TaskId],
) -> dict[TaskId, ScheduleChange]:
    """Push downstream tasks later so every dependency is respected again.

    ``dates`` must already contain the new dates of the ``changed`` tasks.
    Only descendants of ``changed`` are visited, in topological order, so each
    task is evaluated exactly once after all of its prerequisites are final.

    Each task's new start is ``max(prerequisite end) + 1 day``. Because the
    engine takes a maximum instead of adding a delay per path, converging
    paths cannot compound: if A slips 3 days and reaches D through both B and
    C, D moves 3 days, not 6. If one branch had slack, D moves only by the real
    impact. Durations are preserved. Tasks are only ever pushed later, never
    pulled earlier (see Key Assumptions in the README).

    Returns the changes, keyed by task id. ``dates`` is not modified.
    """
    affected = descendants(graph, changed) - set(changed)
    if not affected:
        return {}
    working = dict(dates)
    result: dict[TaskId, ScheduleChange] = {}
    for task in topological_order(graph, affected | set(changed)):
        if task not in affected:
            continue
        current = working[task]
        needed = earliest_start(graph, working, task)
        if needed is not None and current.start < needed:
            moved = current.shifted((needed - current.start).days)
            working[task] = moved
            result[task] = ScheduleChange(task, dates[task], moved)
    return result


# ---------------------------------------------------------------------------
# Bonus: critical path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriticalPathResult:
    slack_days: dict[TaskId, int]
    critical_tasks: list[TaskId]
    critical_chain: list[TaskId]
    project_start: date | None
    project_end: date | None


def critical_path(graph: DependencyGraph, dates: Mapping[TaskId, TaskDates]) -> CriticalPathResult:
    """Total slack per task from a backward pass over the scheduled dates.

    The latest finish of a task is one day before the earliest latest-start of
    its successors, or the project end for tasks without successors. Slack is
    ``latest start - scheduled start``; zero-slack tasks are critical, and the
    longest chain of them from a start task to the project end is returned.
    """
    if not graph.nodes:
        return CriticalPathResult({}, [], [], None, None)
    order = topological_order(graph)
    project_start = min(d.start for d in dates.values())
    project_end = max(d.end for d in dates.values())
    latest_start: dict[TaskId, date] = {}
    for task in reversed(order):
        succs = graph.succ.get(task, ())
        latest_finish = min((latest_start[s] for s in succs), default=project_end + ONE_DAY) - ONE_DAY
        latest_start[task] = latest_finish - timedelta(days=dates[task].duration_days - 1)
    slack = {t: (latest_start[t] - dates[t].start).days for t in order}
    critical = [t for t in order if slack[t] == 0]

    # Walk a zero-slack chain: begin at a critical task with no critical
    # prerequisite and repeatedly follow a critical successor that starts right
    # after the current task ends.
    chain: list[TaskId] = []
    critical_set = set(critical)
    starts = [t for t in critical if not (graph.pred.get(t, set()) & critical_set)]
    if starts:
        node = min(starts, key=lambda t: (dates[t].start, _sort_key(t)))
        chain.append(node)
        while True:
            nxt = [
                s for s in graph.succ.get(node, ())
                if s in critical_set and dates[s].start == dates[node].end + ONE_DAY
            ]
            if not nxt:
                break
            node = min(nxt, key=_sort_key)
            chain.append(node)
    return CriticalPathResult(slack, critical, chain, project_start, project_end)


def _sort_key(value: TaskId) -> tuple[int, str]:
    # Sort ints numerically and everything else as text, without mixing types.
    return (0, f"{value:020d}") if isinstance(value, int) else (1, str(value))
