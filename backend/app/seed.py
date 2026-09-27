"""Demo data: a realistic 10-task software release with a diamond.

Requirements -> (Database schema, Authentication service) -> Backend API is the
diamond from the problem statement. Schema and auth have no slack, so pushing
Requirements out by 3 days moves Backend API by exactly 3 days, not 6.

One real link is left out on purpose (Security review -> Production release);
its description hints at it, so "Suggest dependencies" has something to find.

Run ``python -m app.seed --reset`` to restore the demo board.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AISuggestion, ChangeLog, Dependency, Task

# (key, title, description, status, start offset, duration in days)
TASKS = [
    ("req", "Gather requirements",
     "Interview the operations team and write down the must-have features for the release.",
     "done", 0, 3),
    ("schema", "Design database schema",
     "Model tasks, users and audit tables. Starts after the requirements are signed off.",
     "in_progress", 3, 3),
    ("auth", "Set up authentication service",
     "Single sign-on and role checks. Needs the gathered requirements for the list of roles.",
     "review", 3, 3),
    ("api", "Build backend API",
     "REST endpoints for the board. Requires the database schema and the authentication service.",
     "backlog", 6, 5),
    ("ui", "Build frontend UI",
     "Kanban screens and forms, built from the requirements document.",
     "backlog", 3, 7),
    ("itest", "Write integration tests",
     "End-to-end tests that run once the backend API and the frontend UI are both ready.",
     "backlog", 11, 3),
    ("sec", "Security review",
     "Pen test and code review of the backend API before anything goes to staging.",
     "backlog", 11, 2),
    ("staging", "Deploy to staging",
     "Ship the build to staging after integration tests pass and the security review is signed off.",
     "backlog", 14, 2),
    ("uat", "User acceptance testing",
     "The operations team tries the release on staging, following the staging deploy.",
     "backlog", 16, 3),
    ("release", "Production release",
     "Go live once user acceptance testing is complete and the security review has no open findings.",
     "backlog", 19, 1),
]

LINKS = [
    ("req", "schema"),
    ("req", "auth"),
    ("req", "ui"),
    ("schema", "api"),
    ("auth", "api"),
    ("api", "itest"),
    ("ui", "itest"),
    ("api", "sec"),
    ("itest", "staging"),
    ("sec", "staging"),
    ("staging", "uat"),
    ("uat", "release"),
]


def seed(session: Session, reset: bool = False, start: date | None = None) -> bool:
    """Insert the demo board. Returns False if data exists and reset is False."""
    if reset:
        for model in (AISuggestion, Dependency, ChangeLog, Task):
            session.execute(delete(model))
        session.flush()
    elif session.scalar(select(Task.id).limit(1)) is not None:
        return False

    start = start or date.today()
    ids: dict[str, int] = {}
    per_column: dict[str, int] = {}
    for key, title, description, status, offset, days in TASKS:
        per_column[status] = per_column.get(status, 0) + 1
        task = Task(
            title=title,
            description=description,
            status=status,
            position=per_column[status] * 1024.0,
            start_date=start + timedelta(days=offset),
            end_date=start + timedelta(days=offset + days - 1),
        )
        session.add(task)
        session.flush()
        ids[key] = task.id
    for a, b in LINKS:
        session.add(Dependency(predecessor_id=ids[a], successor_id=ids[b], source="seed"))
    session.commit()
    return True


if __name__ == "__main__":  # pragma: no cover - CLI helper
    from .db import Base, SessionLocal, engine

    parser = argparse.ArgumentParser(description="Load TaskFlow Pro demo data")
    parser.add_argument("--reset", action="store_true", help="delete everything first")
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        print("Seeded demo board." if seed(s, reset=args.reset) else "Data already present; use --reset.")
