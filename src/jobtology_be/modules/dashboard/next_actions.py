from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from jobtology_be.contracts import StepState


class DashboardModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class RoadmapCandidate(DashboardModel):
    roadmap_id: str = Field(min_length=1)
    roadmap_version: int = Field(ge=1)
    is_active: bool


class DeadlineActionCandidate(DashboardModel):
    action_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    due_at: AwareDatetime
    source_is_valid: bool
    prerequisite_step_ids: tuple[str, ...]
    support_refs: tuple[str, ...]


class RoadmapStepCandidate(DashboardModel):
    roadmap_id: str = Field(min_length=1)
    roadmap_version: int = Field(ge=1)
    step_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    state: StepState
    roadmap_position: int = Field(ge=0)
    due_at: AwareDatetime | None
    expires_at: AwareDatetime | None
    source_is_valid: bool
    prerequisite_step_ids: tuple[str, ...]
    support_refs: tuple[str, ...]


class NextActionRequest(DashboardModel):
    reference_at: AwareDatetime
    urgency_window: timedelta = Field(ge=timedelta())
    rule_version: str = Field(min_length=1)
    roadmap: RoadmapCandidate
    completed_step_ids: frozenset[str]
    deadline_actions: tuple[DeadlineActionCandidate, ...]
    roadmap_steps: tuple[RoadmapStepCandidate, ...]


class NextActionRecommendation(DashboardModel):
    kind: Literal["CONTINUE_STEP", "START_STEP", "REVIEW_DEADLINE"]
    roadmap_id: str | None
    roadmap_version: int | None
    step_id: str | None
    title: str
    reason_code: Literal["URGENT_DEADLINE", "IN_PROGRESS_STEP", "NEXT_ROADMAP_STEP"]
    due_at: AwareDatetime | None
    blocked_by: tuple[str, ...]
    support_refs: tuple[str, ...]
    navigation_target: Literal["DEADLINE_ACTION", "ROADMAP_STEP"]
    navigation_target_id: str


class NextActionSelection(DashboardModel):
    rule_version: str
    reference_at: AwareDatetime
    recommendations: tuple[NextActionRecommendation, ...]


def select_next_actions(request: NextActionRequest) -> NextActionSelection:
    urgent_deadlines = sorted(
        (
            action
            for action in request.deadline_actions
            if _is_urgent_deadline(action, request)
        ),
        key=lambda action: (_as_utc(action.due_at), action.action_id),
    )
    in_progress_steps, todo_steps = _eligible_roadmap_steps(request)
    recommendations = (
        *(_recommend_deadline(action) for action in urgent_deadlines),
        *(
            _recommend_step(step, "CONTINUE_STEP", "IN_PROGRESS_STEP")
            for step in in_progress_steps
        ),
        *(_recommend_step(step, "START_STEP", "NEXT_ROADMAP_STEP") for step in todo_steps),
    )
    return NextActionSelection(
        rule_version=request.rule_version,
        reference_at=request.reference_at,
        recommendations=recommendations,
    )


def _is_urgent_deadline(
    action: DeadlineActionCandidate, request: NextActionRequest
) -> bool:
    reference_at = _as_utc(request.reference_at)
    due_at = _as_utc(action.due_at)
    return (
        action.source_is_valid
        and set(action.prerequisite_step_ids).issubset(request.completed_step_ids)
        and reference_at < due_at <= reference_at + request.urgency_window
    )


def _eligible_roadmap_steps(
    request: NextActionRequest,
) -> tuple[list[RoadmapStepCandidate], list[RoadmapStepCandidate]]:
    in_progress_steps: list[RoadmapStepCandidate] = []
    todo_steps: list[RoadmapStepCandidate] = []
    if not request.roadmap.is_active:
        return in_progress_steps, todo_steps

    for step in request.roadmap_steps:
        if not _is_eligible_step(step, request):
            continue
        match step.state:
            case StepState.IN_PROGRESS:
                in_progress_steps.append(step)
            case StepState.TODO:
                todo_steps.append(step)
            case StepState.COMPLETED:
                continue
            case _ as unreachable:
                assert_never(unreachable)

    return (
        sorted(in_progress_steps, key=lambda step: _step_sort_key(step, request.reference_at)),
        sorted(todo_steps, key=lambda step: _step_sort_key(step, request.reference_at)),
    )


def _is_eligible_step(step: RoadmapStepCandidate, request: NextActionRequest) -> bool:
    is_current = step.expires_at is None or _as_utc(step.expires_at) > _as_utc(
        request.reference_at
    )
    belongs_to_active_roadmap = (
        step.roadmap_id == request.roadmap.roadmap_id
        and step.roadmap_version == request.roadmap.roadmap_version
    )
    return (
        belongs_to_active_roadmap
        and step.source_is_valid
        and is_current
        and set(step.prerequisite_step_ids).issubset(request.completed_step_ids)
    )


def _step_sort_key(
    step: RoadmapStepCandidate, reference_at: datetime
) -> tuple[int, bool, datetime, str]:
    return (
        step.roadmap_position,
        step.due_at is None,
        _as_utc(step.due_at) if step.due_at is not None else _as_utc(reference_at),
        step.step_id,
    )


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _recommend_deadline(action: DeadlineActionCandidate) -> NextActionRecommendation:
    return NextActionRecommendation(
        kind="REVIEW_DEADLINE",
        roadmap_id=None,
        roadmap_version=None,
        step_id=None,
        title=action.title,
        reason_code="URGENT_DEADLINE",
        due_at=action.due_at,
        blocked_by=(),
        support_refs=action.support_refs,
        navigation_target="DEADLINE_ACTION",
        navigation_target_id=action.action_id,
    )


def _recommend_step(
    step: RoadmapStepCandidate,
    kind: Literal["CONTINUE_STEP", "START_STEP"],
    reason_code: Literal["IN_PROGRESS_STEP", "NEXT_ROADMAP_STEP"],
) -> NextActionRecommendation:
    return NextActionRecommendation(
        kind=kind,
        roadmap_id=step.roadmap_id,
        roadmap_version=step.roadmap_version,
        step_id=step.step_id,
        title=step.title,
        reason_code=reason_code,
        due_at=step.due_at,
        blocked_by=(),
        support_refs=step.support_refs,
        navigation_target="ROADMAP_STEP",
        navigation_target_id=step.step_id,
    )
