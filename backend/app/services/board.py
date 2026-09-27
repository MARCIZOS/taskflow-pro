"""Board service: loads the graph, asks the engine what should happen, saves it.

Every public function here that writes runs inside ``graph_write_lock`` and a
single transaction. If the engine rejects a change nothing is written, so an
invalid graph can never be persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine as eng
from ..db import graph_write_lock
from ..models import AISuggestion, ChangeLog, Dependency, Task
from ..schemas import (
    BoardOut,
    DependencyOut,
    MutationResult,
    ScheduleChangeOut,
    TaskCreate,
    TaskMove,
    TaskOut,
    TaskUpdate,
    ValidationOut,
)


class ServiceError(Exception):
    """An expected, user-facing error. ``status`` maps onto an HTTP code."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


@dataclass
class Snapshot:
    """Everything the engine needs, loaded in one go."""

    tasks: dict[int, Task]
    deps: list[Dependency]
    graph: eng.DependencyGraph = field(init=False)

    def __post_init__(self):
        self.graph = eng.DependencyGraph.build(self.tasks, [(d.predecessor_id, d.successor_id) for d in self.deps])

    @property
    def statuses(self) -> dict[int, eng.Status]:
        return {tid: eng.Status(t.status) for tid, t in self.tasks.items()}

    @property
    def dates(self) -> dict[int, eng.TaskDates]:
        return {tid: eng.TaskDates(t.start_date, t.end_date) for tid, t in self.tasks.items()}


def load(session: Session) -> Snapshot:
    tasks = {t.id: t for t in session.scalars(select(Task))}
    deps = list(session.scalars(select(Dependency).order_by(Dependency.id)))
    return Snapshot(tasks, deps)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def board(session: Session) -> BoardOut:
    snap = load(session)
    return _board_from(snap)


def _board_from(snap: Snapshot) -> BoardOut:
    ready = eng.readiness(snap.graph, snap.statuses)
    cp = eng.critical_path(snap.graph, snap.dates)
    critical = set(cp.critical_tasks)
    ordered = sorted(snap.tasks.values(), key=lambda t: (t.status, t.position, t.id))
    tasks = [
        TaskOut(
            id=t.id,
            title=t.title,
            description=t.description,
            status=t.status,
            position=t.position,
            start_date=t.start_date,
            end_date=t.end_date,
            duration_days=(t.end_date - t.start_date).days + 1,
            version=t.version,
            predecessor_ids=sorted(snap.graph.pred.get(t.id, ())),
            successor_ids=sorted(snap.graph.succ.get(t.id, ())),
            blocked_by=ready[t.id],
            is_blocked=bool(ready[t.id]),
            slack_days=cp.slack_days.get(t.id),
            is_critical=t.id in critical,
        )
        for t in ordered
    ]
    deps = [
        DependencyOut(id=d.id, predecessor_id=d.predecessor_id, successor_id=d.successor_id, source=d.source)
        for d in snap.deps
    ]
    return BoardOut(
        tasks=tasks,
        dependencies=deps,
        critical_chain=list(cp.critical_chain),
        project_start=cp.project_start,
        project_end=cp.project_end,
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def create_task(session: Session, data: TaskCreate) -> MutationResult:
    with graph_write_lock(session):
        position = _end_of_column(session, "backlog")
        task = Task(
            title=data.title,
            description=data.description,
            status="backlog",
            position=position,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        session.add(task)
        session.flush()
        _log(session, task.id, "task_created", {"title": task.title})
        session.commit()
    return MutationResult(board=board(session), message=f"Created '{task.title}'")


def update_task(session: Session, task_id: int, data: TaskUpdate) -> MutationResult:
    with graph_write_lock(session):
        snap = load(session)
        task = _get(snap, task_id)
        _check_version(task, data.version)

        if data.title is not None:
            task.title = data.title
        if data.description is not None:
            task.description = data.description

        new_start = data.start_date or task.start_date
        new_end = data.end_date or task.end_date
        dates_changed = (new_start, new_end) != (task.start_date, task.end_date)
        changes: dict[int, eng.ScheduleChange] = {}
        if dates_changed:
            if new_end < new_start:
                raise ServiceError(422, "invalid_dates", "End date cannot be before start date")
            dates = snap.dates
            earliest = eng.earliest_start(snap.graph, dates, task_id)
            if earliest is not None and new_start < earliest:
                late = max(snap.graph.pred[task_id], key=lambda p: dates[p].end)
                raise ServiceError(
                    422,
                    "starts_before_prerequisite",
                    f"'{task.title}' cannot start before '{snap.tasks[late].title}' finishes on "
                    f"{_fmt(dates[late].end)}. Earliest possible start is {_fmt(earliest)}.",
                    earliest_start=earliest.isoformat(),
                    blocking_task_id=late,
                )
            before = dates[task_id]
            dates[task_id] = eng.TaskDates(new_start, new_end)
            changes = eng.propagate_schedule(snap.graph, dates, [task_id])
            task.start_date, task.end_date = new_start, new_end
            _apply_schedule(snap, changes)
            _log(
                session,
                task_id,
                "dates_changed",
                {"from": [str(before.start), str(before.end)], "to": [str(new_start), str(new_end)],
                 "downstream_moved": {str(k): v.shift_days for k, v in changes.items()}},
            )
        session.commit()
    return MutationResult(
        board=board(session),
        schedule_changes=_changes_out(snap, changes),
        message=_schedule_message(changes) if dates_changed else "Task updated",
    )


def move_task(session: Session, task_id: int, data: TaskMove) -> MutationResult:
    with graph_write_lock(session):
        snap = load(session)
        task = _get(snap, task_id)
        _check_version(task, data.version)
        before = snap.statuses
        target = eng.Status(data.status)

        blocking = eng.check_status_change(snap.graph, before, task_id, target)
        if blocking:
            names = ", ".join(f"'{snap.tasks[b].title}'" for b in blocking)
            raise ServiceError(
                409,
                "blocked",
                f"'{task.title}' is blocked. Finish {names} first.",
                blockers=blocking,
            )

        old_status = task.status
        task.status = data.status
        task.position = _position_at(snap, data.status, data.index, exclude=task_id)
        after = {**before, task_id: target}
        newly_blocked = eng.newly_blocked_by_regression(snap.graph, before, after)
        if old_status != data.status:
            _log(session, task_id, "status_changed", {"from": old_status, "to": data.status,
                                                     "newly_blocked": newly_blocked})
        session.commit()

    message = ""
    if newly_blocked:
        names = ", ".join(f"'{snap.tasks[t].title}'" for t in newly_blocked)
        message = f"'{task.title}' is no longer Done, so {names} {'is' if len(newly_blocked) == 1 else 'are'} blocked again."
    return MutationResult(board=board(session), newly_blocked=newly_blocked, message=message)


def delete_task(session: Session, task_id: int) -> MutationResult:
    with graph_write_lock(session):
        snap = load(session)
        task = _get(snap, task_id)
        title = task.title
        for dep in snap.deps:
            if task_id in (dep.predecessor_id, dep.successor_id):
                session.delete(dep)
        for s in session.scalars(
            select(AISuggestion).where(
                (AISuggestion.predecessor_id == task_id) | (AISuggestion.successor_id == task_id)
            )
        ):
            session.delete(s)
        session.delete(task)
        _log(session, task_id, "task_deleted", {"title": title})
        session.commit()
    return MutationResult(board=board(session), message=f"Deleted '{title}'")


def check_dependency(session: Session, predecessor_id: int, successor_id: int) -> ValidationOut:
    """Dry run: would this dependency be accepted? Nothing is written."""
    snap = load(session)
    try:
        eng.validate_new_edge(snap.graph, predecessor_id, successor_id)
    except eng.DependencyError as exc:
        return ValidationOut(valid=False, **_dependency_error(snap, exc).to_dict())
    return ValidationOut(valid=True)


def add_dependency(session: Session, predecessor_id: int, successor_id: int, source: str = "manual") -> MutationResult:
    with graph_write_lock(session):
        snap = load(session)
        try:
            eng.validate_new_edge(snap.graph, predecessor_id, successor_id)
        except eng.DependencyError as exc:
            raise _dependency_error(snap, exc) from exc

        session.add(Dependency(predecessor_id=predecessor_id, successor_id=successor_id, source=source))
        snap.graph.add_edge(predecessor_id, successor_id)

        # The new link may make the successor start too early. Move it (and
        # everything after it) just enough to respect the link.
        dates = snap.dates
        changes: dict[int, eng.ScheduleChange] = {}
        needed = dates[predecessor_id].end + timedelta(days=1)
        if dates[successor_id].start < needed:
            first = eng.ScheduleChange(
                successor_id, dates[successor_id], dates[successor_id].shifted((needed - dates[successor_id].start).days)
            )
            dates[successor_id] = first.new
            changes = {successor_id: first, **eng.propagate_schedule(snap.graph, dates, [successor_id])}
            _apply_schedule(snap, changes)

        pred, succ = snap.tasks[predecessor_id], snap.tasks[successor_id]
        _log(session, successor_id, "dependency_added",
             {"predecessor": predecessor_id, "source": source,
              "moved": {str(k): v.shift_days for k, v in changes.items()}})
        session.commit()
    msg = f"'{succ.title}' now depends on '{pred.title}'."
    if changes:
        msg += " " + _schedule_message(changes)
    return MutationResult(board=board(session), schedule_changes=_changes_out(snap, changes), message=msg)


def remove_dependency(session: Session, dependency_id: int) -> MutationResult:
    with graph_write_lock(session):
        dep = session.get(Dependency, dependency_id)
        if dep is None:
            raise ServiceError(404, "not_found", "Dependency not found")
        _log(session, dep.successor_id, "dependency_removed", {"predecessor": dep.predecessor_id})
        session.delete(dep)
        session.commit()
    return MutationResult(board=board(session), message="Dependency removed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(snap: Snapshot, task_id: int) -> Task:
    task = snap.tasks.get(task_id)
    if task is None:
        raise ServiceError(404, "not_found", "Task not found")
    return task


def _check_version(task: Task, version: int) -> None:
    if task.version != version:
        raise ServiceError(
            409,
            "stale_version",
            f"'{task.title}' was changed somewhere else. The board has been refreshed, please try again.",
            current_version=task.version,
        )


def _dependency_error(snap: Snapshot, exc: eng.DependencyError) -> ServiceError:
    if isinstance(exc, eng.CycleError):
        names = " → ".join(snap.tasks[t].title for t in exc.cycle)
        return ServiceError(409, exc.code, f"That would create a loop: {names}", cycle=list(exc.cycle))
    status = 404 if isinstance(exc, eng.UnknownTaskError) else 409
    return ServiceError(status, exc.code, str(exc))


def _apply_schedule(snap: Snapshot, changes: dict[int, eng.ScheduleChange]) -> None:
    for tid, change in changes.items():
        snap.tasks[tid].start_date = change.new.start
        snap.tasks[tid].end_date = change.new.end


def _changes_out(snap: Snapshot, changes: dict[int, eng.ScheduleChange]) -> list[ScheduleChangeOut]:
    return [
        ScheduleChangeOut(
            task_id=tid,
            title=snap.tasks[tid].title,
            old_start=c.old.start,
            old_end=c.old.end,
            new_start=c.new.start,
            new_end=c.new.end,
            shift_days=c.shift_days,
        )
        for tid, c in sorted(changes.items(), key=lambda kv: (kv[1].new.start, kv[0]))
    ]


def _schedule_message(changes: dict[int, eng.ScheduleChange]) -> str:
    if not changes:
        return "Dates updated. No downstream task had to move."
    n = len(changes)
    return f"Dates updated. {n} downstream task{'s' if n != 1 else ''} moved to keep dependencies valid."


def _column(snap: Snapshot, status: str, exclude: int | None = None) -> list[Task]:
    return sorted(
        (t for t in snap.tasks.values() if t.status == status and t.id != exclude),
        key=lambda t: (t.position, t.id),
    )


def _position_at(snap: Snapshot, status: str, index: int, exclude: int) -> float:
    column = _column(snap, status, exclude)
    index = min(index, len(column))
    before = column[index - 1].position if index > 0 else None
    after = column[index].position if index < len(column) else None
    if before is None and after is None:
        return 1024.0
    if before is None:
        return after - 1024.0
    if after is None:
        return before + 1024.0
    if after - before < 1e-6:
        # Ran out of room between two ranks: renumber the column evenly.
        for i, t in enumerate(column):
            t.position = (i + 1) * 1024.0
        return index * 1024.0 + 512.0
    return (before + after) / 2


def _end_of_column(session: Session, status: str) -> float:
    positions = session.scalars(select(Task.position).where(Task.status == status)).all()
    return (max(positions) + 1024.0) if positions else 1024.0


def _log(session: Session, task_id: int | None, kind: str, detail: dict) -> None:
    session.add(ChangeLog(task_id=task_id, kind=kind, detail=json.dumps(detail, default=str)))


def _fmt(d: date) -> str:
    return d.strftime("%b %d").replace(" 0", " ")
