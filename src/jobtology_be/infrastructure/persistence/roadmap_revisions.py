"""Build reviewed revisions without mutating the immutable source proposal."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.application.roadmap_revisions import (
    PreservedCompletedStep,
    RevisionScheduleChange,
    RoadmapRevisionView,
)
from jobtology_be.contracts import RoadmapOutcome
from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    MissingRecordError,
    PersistenceConflictError,
)
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    goals,
    profiles,
    roadmap_plan_overrides,
    roadmap_steps,
    roadmaps,
    route_proposals,
    step_completion_inheritances,
    user_state_events,
)


class _Step(BaseModel):
    model_config = ConfigDict(frozen=True)
    step_id: UUID
    step_key: str
    position: int
    action_id: str
    template_revision: int
    state: str
    planned_start: datetime | None
    planned_end: datetime | None
    outcomes: tuple[RoadmapOutcome, ...]
    criteria: tuple[str, ...]
    completion_event_id: UUID | None
    completion_owner: UUID | None
    completion_kind: str | None
    completion_payload: dict[str, JsonValue] | None

    @property
    def semantics(self) -> tuple:
        return (self.action_id, self.template_revision,
                tuple((item.requirement_key, item.entity_id, item.raw_text, item.experience_codes)
                      for item in self.outcomes), self.criteria)


class _ProposedStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    step_key: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=128)
    template_revision: int = Field(ge=1)
    title: str
    estimated_hours: int = Field(gt=0)
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    outcomes: tuple[RoadmapOutcome, ...]
    completion_criteria: tuple[str, ...] = ()
    prerequisite_step_keys: tuple[str, ...] = ()

    @property
    def semantics(self) -> tuple:
        return (self.action_id, self.template_revision,
                tuple((item.requirement_key, item.entity_id, item.raw_text, item.experience_codes)
                      for item in self.outcomes), self.completion_criteria)


@dataclass(frozen=True, slots=True)
class RevisionContext:
    view: RoadmapRevisionView
    goal_id: UUID
    title: str
    old_analysis_id: UUID
    old_proposal_id: UUID
    old_steps: tuple[_Step, ...]
    old_documents: Mapping[str, Mapping[str, JsonValue]]
    proposed_steps: tuple[_ProposedStep, ...]
    old_results: Mapping[str, JsonValue] | None
    plan: Mapping[str, JsonValue] | None
    old_release_id: str | None


def _occupation(snapshot: Mapping, results: Mapping | None) -> str | None:
    selected = snapshot.get('snapshot_selection')
    first = selected.get('occupation_id') if isinstance(selected, Mapping) else None
    second = results.get('occupation_id') if isinstance(results, Mapping) else None
    choices = {value for value in (first, second) if isinstance(value, str) and value.strip()}
    return next(iter(choices)) if len(choices) == 1 else None


async def load_revision(
    session: AsyncSession, user_id: UUID, roadmap_id: UUID, proposal_id: UUID, *, lock: bool = False,
) -> RevisionContext:
    statement = select(roadmaps).where(roadmaps.c.id == roadmap_id, roadmaps.c.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    roadmap = (await session.execute(statement)).mappings().one_or_none()
    if roadmap is None:
        raise MissingRecordError(resource='roadmap')
    current_profile = (await session.execute(select(
        profiles.c.version, profiles.c.latest_analysis_id,
    ).where(profiles.c.user_id == user_id))).one_or_none()
    if current_profile is None:
        raise MissingRecordError(resource='profile')
    proposal = (await session.execute(select(
        route_proposals.c.proposal_hash, route_proposals.c.profile_version, route_proposals.c.analysis_id,
        route_proposals.c.feasibility, route_proposals.c.steps, route_proposals.c.constraints_snapshot,
        analyses.c.goal_id, analyses.c.release_id, analyses.c.status.label('analysis_status'),
        analyses.c.input_snapshot, analyses.c.results, goals.c.status.label('goal_status'),
        goals.c.goal_mode, goals.c.occupation_id,
    ).join(analyses, analyses.c.id == route_proposals.c.analysis_id)
      .join(goals, goals.c.id == analyses.c.goal_id)
      .where(route_proposals.c.id == proposal_id, route_proposals.c.user_id == user_id,
             analyses.c.user_id == user_id, goals.c.user_id == user_id))).one_or_none()
    if proposal is None or proposal.goal_id != roadmap['goal_id']:
        raise MissingRecordError(resource='route proposal')
    old = (await session.execute(select(
        route_proposals.c.steps, route_proposals.c.constraints_snapshot, route_proposals.c.analysis_id,
        analyses.c.results, analyses.c.release_id,
    ).join(analyses, analyses.c.id == route_proposals.c.analysis_id)
      .where(route_proposals.c.id == roadmap['proposal_id'], route_proposals.c.user_id == user_id,
             analyses.c.user_id == user_id))).one_or_none()
    if old is None:
        raise MissingRecordError(resource='route proposal')
    rows = (await session.execute(select(
        roadmap_steps.c.id.label('step_id'), roadmap_steps.c.step_key, roadmap_steps.c.position,
        roadmap_steps.c.action_id, roadmap_steps.c.template_revision, roadmap_steps.c.state,
        roadmap_steps.c.planned_start, roadmap_steps.c.planned_end, roadmap_steps.c.outcomes,
        roadmap_steps.c.criteria,
        step_completion_inheritances.c.original_completion_event_id.label('completion_event_id'),
        user_state_events.c.user_id.label('completion_owner'), user_state_events.c.kind.label('completion_kind'),
        user_state_events.c.payload.label('completion_payload'),
    ).outerjoin(step_completion_inheritances, step_completion_inheritances.c.step_id == roadmap_steps.c.id)
      .outerjoin(user_state_events, user_state_events.c.id == step_completion_inheritances.c.original_completion_event_id)
      .where(roadmap_steps.c.roadmap_id == roadmap_id)
      .order_by(roadmap_steps.c.position, roadmap_steps.c.id))).mappings().all()
    try:
        old_steps = tuple(_Step.model_validate(row) for row in rows)
        proposed = tuple(_ProposedStep.model_validate(item) for item in proposal.steps)
    except ValidationError as error:
        raise PersistenceConflictError(resource='revision step data') from error
    plan = (await session.execute(select(roadmap_plan_overrides).where(
        roadmap_plan_overrides.c.roadmap_id == roadmap_id,
    ))).mappings().one_or_none()
    original_docs = {item['step_key']: item for item in old.steps if isinstance(item, Mapping)}
    history = roadmap['validity'].get('revision_history_steps', {})
    if isinstance(history, Mapping):
        original_docs.update({key: item for key, item in history.items() if isinstance(item, Mapping)})
    proposed_semantics = {item.semantics for item in proposed}
    proposed_by_key = {item.step_key: item for item in proposed}
    old_by_key = {item.step_key: item for item in old_steps}
    completed = tuple(item for item in old_steps if item.state == 'COMPLETED')
    blocking: list[str] = []
    if roadmap['state'] != 'ACTIVE':
        blocking.append('ACTIVE_ROADMAP_REQUIRED')
    if proposal_id == roadmap['proposal_id']:
        blocking.append('DIFFERENT_PROPOSAL_REQUIRED')
    if current_profile.version != proposal.profile_version or current_profile.latest_analysis_id != proposal.analysis_id:
        blocking.append('STALE_PROPOSAL')
    if (proposal.goal_status != 'ACTIVE' or proposal.goal_mode != 'TARGETED'
            or _occupation(proposal.input_snapshot, proposal.results) != proposal.occupation_id
            or proposal.occupation_id is None):
        blocking.append('ACTIVE_TARGETED_GOAL_REQUIRED')
    if proposal.feasibility not in {'FEASIBLE', 'RISKY', 'PARTIAL'} or proposal.analysis_status != 'READY':
        blocking.append('PROPOSAL_NOT_ACTIONABLE')
    if any(not _verified_completion(item, user_id) for item in completed):
        blocking.append('COMPLETED_HISTORY_UNVERIFIED')
    saved_schedules = plan['step_schedules'] if plan is not None else {}
    for item in completed:
        document = original_docs.get(item.step_key, {})
        snapshot = saved_schedules.get(str(item.step_id), {}) if isinstance(saved_schedules, Mapping) else {}
        hours = document.get('estimated_hours') or (snapshot.get('hours') if isinstance(snapshot, Mapping) else None)
        if not isinstance(hours, int) or isinstance(hours, bool) or hours < 1:
            blocking.append('COMPLETED_HISTORY_UNAVAILABLE')
            break
    if any(set(item.prerequisite_step_keys) - set(proposed_by_key) for item in proposed):
        blocking.append('PROPOSAL_NOT_ACTIONABLE')
    if any((item.planned_start_at is not None and item.planned_start_at.utcoffset() is None)
           or (item.planned_end_at is not None and item.planned_end_at.utcoffset() is None)
           or (item.planned_start_at is not None and item.planned_end_at is not None
               and item.planned_end_at < item.planned_start_at) for item in proposed):
        blocking.append('PROPOSAL_NOT_ACTIONABLE')
    target = proposal.constraints_snapshot.get('target_by')
    try:
        if not isinstance(target, str) or datetime.fromisoformat(target).astimezone(UTC) <= datetime.now(UTC):
            blocking.append('PROPOSAL_DEADLINE_PASSED')
    except ValueError:
        blocking.append('PROPOSAL_NOT_ACTIONABLE')
    events = tuple(item.completion_event_id for item in completed)
    if len(set(events)) != len(events):
        blocking.append('DUPLICATE_COMPLETION_HISTORY')
    if any(f'completed:{item.completion_event_id}' in proposed_by_key
           for item in completed if item.semantics not in proposed_semantics):
        blocking.append('HISTORY_STEP_KEY_CONFLICT')
    if len(proposed_by_key) != len(proposed):
        blocking.append('DUPLICATE_PROPOSAL_STEPS')
    matched_completed_semantics = {item.semantics for item in completed if item.semantics in proposed_semantics}
    carried_count = len(completed) - len(matched_completed_semantics)
    current_constraints = dict(old.constraints_snapshot)
    if plan is not None and plan['is_customized']:
        current_constraints['available_hours_per_week'] = plan['weekly_hours']
    constraints = {
        key: (current_constraints.get(key), proposal.constraints_snapshot.get(key))
        for key in sorted(set(current_constraints) | set(proposal.constraints_snapshot))
        if current_constraints.get(key) != proposal.constraints_snapshot.get(key)
    }
    schedules = plan['step_schedules'] if plan is not None else {}
    def current_date(item: _Step, field: str) -> datetime | None:
        snapshot = schedules.get(str(item.step_id), {}) if isinstance(schedules, Mapping) else {}
        value = snapshot.get(field) if isinstance(snapshot, Mapping) else None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.utcoffset() is not None:
                    return parsed
            except ValueError:
                pass
        return item.planned_start if field == 'planned_start_at' else item.planned_end
    changes = tuple(RevisionScheduleChange(
        step_key=item.step_key, old_planned_start=current_date(old_by_key[item.step_key], 'planned_start_at'),
        old_planned_end=current_date(old_by_key[item.step_key], 'planned_end_at'),
        new_planned_start=item.planned_start_at, new_planned_end=item.planned_end_at,
    ) for item in proposed if item.step_key in old_by_key
        and old_by_key[item.step_key].state != 'COMPLETED'
        and (current_date(old_by_key[item.step_key], 'planned_start_at') != item.planned_start_at
             or current_date(old_by_key[item.step_key], 'planned_end_at') != item.planned_end_at))
    view = RoadmapRevisionView(
        roadmap_id=roadmap_id, roadmap_version=roadmap['version'], profile_version=current_profile.version,
        plan_version=plan['version'] if plan else 0, proposal_id=proposal_id,
        proposal_hash=proposal.proposal_hash, analysis_id=proposal.analysis_id, release_id=proposal.release_id,
        feasibility=proposal.feasibility, can_apply=not blocking,
        resets_future_customization=bool(plan and plan['is_customized']), blocking_reasons=tuple(dict.fromkeys(blocking)),
        preserved_completed_steps=tuple(PreservedCompletedStep(
            item.step_id, item.step_key, str(original_docs.get(item.step_key, {}).get('title') or item.action_id),
        ) for item in completed),
        retained_in_progress_step_ids=tuple(item.step_id for item in old_steps
                                             if item.state == 'IN_PROGRESS' and item.semantics in proposed_semantics),
        unretained_in_progress_step_ids=tuple(item.step_id for item in old_steps
                                               if item.state == 'IN_PROGRESS' and item.semantics not in proposed_semantics),
        added_step_keys=tuple(item.step_key for item in proposed
                             if item.step_key not in old_by_key or item.semantics != old_by_key[item.step_key].semantics),
        removed_step_keys=tuple(item.step_key for item in old_steps if item.state != 'COMPLETED'
                               and (item.step_key not in proposed_by_key
                                    or item.semantics != proposed_by_key[item.step_key].semantics)),
        revised_step_count=len(proposed) + carried_count,
        constraint_changes=constraints, schedule_changes=changes,
    )
    return RevisionContext(view, roadmap['goal_id'], roadmap['title'], old.analysis_id,
                           roadmap['proposal_id'], old_steps, original_docs, proposed, old.results, plan, old.release_id)


async def preserve_revision_progress(
    session: AsyncSession, context: RevisionContext, new_roadmap_id: UUID,
) -> int:
    """Retain verified history; never create a completion event or competency credit."""
    rows = (await session.execute(select(roadmap_steps.c.id, roadmap_steps.c.step_key).where(
        roadmap_steps.c.roadmap_id == new_roadmap_id,
    ))).all()
    new_ids = {row.step_key: row.id for row in rows}
    by_semantics = {item.semantics: item for item in context.proposed_steps}
    histories: dict[str, JsonValue] = {}
    snapshots: dict[str, JsonValue] = {}
    old_snapshots = context.plan.get('step_schedules', {}) if context.plan else {}
    old_snapshots = old_snapshots if isinstance(old_snapshots, Mapping) else {}
    next_position = len(rows)
    preserved = 0
    matched_completed: set[tuple] = set()
    all_completed_semantics = {item.semantics for item in context.old_steps if item.state == 'COMPLETED'}
    stored_requirements = (context.old_results or {}).get('requirements', ())
    stored_requirements = {item['requirement_key']: item for item in stored_requirements
                           if isinstance(item, Mapping) and isinstance(item.get('requirement_key'), str)} if isinstance(stored_requirements, (list, tuple)) else {}
    for step in context.old_steps:
        matched = by_semantics.get(step.semantics)
        if step.state == 'IN_PROGRESS' and matched is not None and step.semantics not in all_completed_semantics:
            await session.execute(update(roadmap_steps).where(roadmap_steps.c.id == new_ids[matched.step_key])
                                  .values(state='IN_PROGRESS'))
        if step.state != 'COMPLETED':
            continue
        preserved += 1
        if matched is not None and step.semantics not in matched_completed:
            matched_completed.add(step.semantics)
            step_id, key = new_ids[matched.step_key], matched.step_key
            await session.execute(update(roadmap_steps).where(roadmap_steps.c.id == step_id).values(
                state='COMPLETED', planned_start=step.planned_start, planned_end=step.planned_end,
            ))
            await session.execute(update(step_completion_inheritances)
                                  .where(step_completion_inheritances.c.step_id == step_id)
                                  .values(original_completion_event_id=step.completion_event_id))
        else:
            step_id, key = uuid4(), f'completed:{step.completion_event_id}'
            await session.execute(insert(roadmap_steps).values(
                id=step_id, roadmap_id=new_roadmap_id, step_key=key, position=next_position,
                action_id=step.action_id, template_revision=step.template_revision, state='COMPLETED',
                planned_start=step.planned_start, planned_end=step.planned_end,
                outcomes=[item.model_dump(mode='json') for item in step.outcomes], criteria=list(step.criteria),
            ))
            next_position += 1
            await session.execute(insert(step_completion_inheritances).values(
                step_id=step_id, original_completion_event_id=step.completion_event_id,
            ))
        original = dict(context.old_documents.get(step.step_key, {}))
        original['step_key'] = key
        original.setdefault('source_step_id', str(step.step_id))
        original.setdefault('source_roadmap_id', str(context.view.roadmap_id))
        original.setdefault('source_analysis_id', str(context.old_analysis_id))
        original.setdefault('source_release_id', context.old_release_id)
        original.setdefault('source_requirements', [stored_requirements[item.requirement_key]
                                                   for item in step.outcomes
                                                   if item.requirement_key in stored_requirements])
        original.setdefault('requirement_metadata', {
            item.requirement_key: stored_requirements.get(item.requirement_key, {}).get('metadata')
            for item in step.outcomes
        })
        histories[key] = original
        snapshot = old_snapshots.get(str(step.step_id))
        if isinstance(snapshot, Mapping):
            snapshots[str(step_id)] = {**snapshot, 'step_id': str(step_id), 'state': 'COMPLETED'}
    validity = {
        'revision_from_roadmap_id': str(context.view.roadmap_id),
        'revision_history_steps': histories,
    }
    await session.execute(update(roadmaps).where(roadmaps.c.id == new_roadmap_id).values(validity=validity))
    if snapshots:
        await session.execute(insert(roadmap_plan_overrides).values(
            roadmap_id=new_roadmap_id, weekly_hours=10, rest_weeks=[], step_order=[], step_hours={},
            version=1, is_customized=False, step_schedules=snapshots,
        ))
    return preserved


def _verified_completion(step: _Step, user_id: UUID) -> bool:
    if (step.completion_event_id is None or step.completion_owner != user_id
            or step.completion_kind != 'STEP_COMPLETED' or step.completion_payload is None):
        return False
    outcomes = step.completion_payload.get('outcomes')
    if not isinstance(outcomes, (list, tuple)):
        return False
    try:
        recorded = tuple(RoadmapOutcome.model_validate(item) for item in outcomes)
    except ValidationError:
        return False
    return recorded == step.outcomes
