"""HTTP layer. Thin on purpose: validate input, call a service, return JSON."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .models import ChangeLog
from .schemas import (
    AIMetricsOut,
    BoardOut,
    DependencyCreate,
    MutationResult,
    SuggestionOut,
    SuggestRunOut,
    TaskCreate,
    TaskMove,
    TaskUpdate,
    ValidationOut,
)
from .services import ai as ai_service
from .services import board as board_service
from .services.board import ServiceError

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/board", response_model=BoardOut)
def get_board(session: Session = Depends(get_session)):
    return board_service.board(session)


@router.post("/tasks", response_model=MutationResult, status_code=201)
def create_task(body: TaskCreate, session: Session = Depends(get_session)):
    return board_service.create_task(session, body)


@router.patch("/tasks/{task_id}", response_model=MutationResult)
def update_task(task_id: int, body: TaskUpdate, session: Session = Depends(get_session)):
    return board_service.update_task(session, task_id, body)


@router.post("/tasks/{task_id}/move", response_model=MutationResult)
def move_task(task_id: int, body: TaskMove, session: Session = Depends(get_session)):
    return board_service.move_task(session, task_id, body)


@router.delete("/tasks/{task_id}", response_model=MutationResult)
def delete_task(task_id: int, session: Session = Depends(get_session)):
    return board_service.delete_task(session, task_id)


@router.post("/dependencies", response_model=MutationResult, status_code=201)
def add_dependency(body: DependencyCreate, session: Session = Depends(get_session)):
    return board_service.add_dependency(session, body.predecessor_id, body.successor_id)


@router.post("/dependencies/validate", response_model=ValidationOut)
def validate_dependency(body: DependencyCreate, session: Session = Depends(get_session)):
    return board_service.check_dependency(session, body.predecessor_id, body.successor_id)


@router.delete("/dependencies/{dependency_id}", response_model=MutationResult)
def remove_dependency(dependency_id: int, session: Session = Depends(get_session)):
    return board_service.remove_dependency(session, dependency_id)


@router.post("/ai/suggestions", response_model=SuggestRunOut)
def run_suggestions(session: Session = Depends(get_session)):
    return ai_service.suggest(session)


@router.get("/ai/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    status: str | None = Query("pending", pattern="^(pending|accepted|rejected|stale)$"),
    session: Session = Depends(get_session),
):
    return ai_service.list_suggestions(session, status)


@router.post("/ai/suggestions/{suggestion_id}/accept", response_model=MutationResult)
def accept_suggestion(suggestion_id: int, session: Session = Depends(get_session)):
    return ai_service.accept(session, suggestion_id)


@router.post("/ai/suggestions/{suggestion_id}/reject", status_code=204)
def reject_suggestion(suggestion_id: int, session: Session = Depends(get_session)):
    ai_service.reject(session, suggestion_id)
    return Response(status_code=204)


@router.get("/ai/metrics", response_model=AIMetricsOut)
def ai_metrics(session: Session = Depends(get_session)):
    return ai_service.metrics(session)


@router.get("/history")
def history(limit: int = Query(30, ge=1, le=200), session: Session = Depends(get_session)):
    rows = session.scalars(select(ChangeLog).order_by(ChangeLog.id.desc()).limit(limit))
    return [
        {"id": r.id, "task_id": r.task_id, "kind": r.kind, "detail": r.detail, "at": r.created_at.isoformat()}
        for r in rows
    ]


@router.post("/demo/reset", response_model=BoardOut)
def reset_demo(session: Session = Depends(get_session)):
    if not get_settings().allow_demo_reset:
        raise ServiceError(403, "disabled", "Demo reset is disabled on this server.")
    from .seed import seed

    seed(session, reset=True)
    ai_service.rate_limiter.reset()
    return board_service.board(session)
