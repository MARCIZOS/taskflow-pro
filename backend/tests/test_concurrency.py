"""Two requests racing to add opposite links must not both succeed."""

import threading
from datetime import date

from app.models import Dependency, Task
from app.services import board
from app.services.board import ServiceError


def test_racing_opposite_links_cannot_create_a_cycle(session_factory):
    with session_factory() as s:
        a = Task(title="A", start_date=date(2026, 10, 1), end_date=date(2026, 10, 2))
        b = Task(title="B", start_date=date(2026, 10, 1), end_date=date(2026, 10, 2))
        s.add_all([a, b])
        s.commit()
        a_id, b_id = a.id, b.id

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt(pred, succ):
        with session_factory() as s:
            barrier.wait()
            try:
                board.add_dependency(s, pred, succ)
                outcomes.append("ok")
            except ServiceError as exc:
                outcomes.append(exc.code)

    threads = [threading.Thread(target=attempt, args=pair) for pair in ((a_id, b_id), (b_id, a_id))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["cycle", "ok"]
    with session_factory() as s:
        assert s.query(Dependency).count() == 1
