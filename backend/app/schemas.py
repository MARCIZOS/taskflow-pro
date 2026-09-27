from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StatusName = Literal["backlog", "in_progress", "review", "done"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreate(_Strict):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates_in_order(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class TaskUpdate(_Strict):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    start_date: date | None = None
    end_date: date | None = None


class TaskMove(_Strict):
    version: int = Field(ge=1)
    status: StatusName
    index: int = Field(ge=0, description="Target position inside the column, 0 = top")


class DependencyCreate(_Strict):
    predecessor_id: int
    successor_id: int


class TaskOut(BaseModel):
    id: int
    title: str
    description: str
    status: StatusName
    position: float
    start_date: date
    end_date: date
    duration_days: int
    version: int
    predecessor_ids: list[int]
    successor_ids: list[int]
    blocked_by: list[int]
    is_blocked: bool
    slack_days: int | None
    is_critical: bool


class DependencyOut(BaseModel):
    id: int
    predecessor_id: int
    successor_id: int
    source: str


class BoardOut(BaseModel):
    tasks: list[TaskOut]
    dependencies: list[DependencyOut]
    critical_chain: list[int]
    project_start: date | None
    project_end: date | None


class ScheduleChangeOut(BaseModel):
    task_id: int
    title: str
    old_start: date
    old_end: date
    new_start: date
    new_end: date
    shift_days: int


class MutationResult(BaseModel):
    board: BoardOut
    schedule_changes: list[ScheduleChangeOut] = []
    newly_blocked: list[int] = []
    message: str = ""


class ValidationOut(BaseModel):
    valid: bool
    code: str | None = None
    message: str | None = None
    cycle: list[int] | None = None


# --- AI suggestions --------------------------------------------------------


class RawSuggestion(BaseModel):
    """Shape the model must return for each suggestion (validation gate 1)."""

    model_config = ConfigDict(extra="ignore")

    predecessor_id: int
    successor_id: int
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=3, max_length=400)
    evidence: str = Field(min_length=3, max_length=300)

    @field_validator("reason", "evidence")
    @classmethod
    def _clean(cls, value: str) -> str:
        return " ".join(value.split())


class RawSuggestionList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suggestions: list[RawSuggestion] = Field(default_factory=list, max_length=50)


class SuggestionOut(BaseModel):
    id: int
    predecessor_id: int
    successor_id: int
    predecessor_title: str
    successor_title: str
    confidence: float
    rationale: str
    evidence: str
    model: str
    status: str


class SuggestRunOut(BaseModel):
    provider: str
    model: str
    used_fallback: bool
    fallback_reason: str | None
    proposed: int
    shown: int
    dropped: dict[str, int]
    suggestions: list[SuggestionOut]


class AIMetricsOut(BaseModel):
    shown: int
    accepted: int
    rejected: int
    pending: int
    precision: float | None = Field(description="accepted / (accepted + rejected)")
