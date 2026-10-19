from dataclasses import replace
from datetime import timedelta

import pytest
from test_recompute_worker import (
    REFERENCE_TIME,
    RecordingFinalizer,
    StaticContextReader,
    StaticSnapshotReader,
    _context,
    _snapshot,
    _work_item,
)

from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedSnapshotSelection
from jobtology_be.modules.analyses.editorial_models import (
    EditorialRequirement,
    RequirementNecessity,
)
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.planning.candidate_models import ActivityTemplate, KnownKrwCost, UnknownKrwCost
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import CandidateAvailability, PlanningSlot
from jobtology_be.product_roles.planning import (
    PRODUCT_PLANNING_POLICY_VERSION,
    product_planning_catalog,
)
from jobtology_be.workers.recompute import RecomputeWorker


def product_snapshot(*, prerequisite=False):
    original = _snapshot()
    return replace(
        original, basis_version='product-roles-v1',
        release=replace(original.release, release_id=f'product-roles-v1-{"a" * 64}'),
        capability_entries=tuple(CapabilityCatalogEntry(
            entity_id=f'capability-{key}', aliases=frozenset({key}),
        ) for key in ('foundation', 'core')),
        requirements=tuple(EditorialRequirement(
            requirement_key=key, label=key, necessity=RequirementNecessity.REQUIRED,
            entity_id=f'capability-{key}', required_experience_codes=frozenset(),
            support_refs=frozenset({f'ncs:{key}'}),
        ) for key in ('foundation', 'core')),
        templates=tuple(ActivityTemplate(
            action_id=action, revision=1, title=f'{key} 학습', estimated_hours=2,
            outcome_requirement_keys=frozenset({key}),
            prerequisite_action_ids=('study:z-foundation',) if key == 'core' and prerequisite else (),
            completion_criteria=(f'{key} 실습',), support_refs=frozenset({f'ncs:{key}'}),
            cost=UnknownKrwCost(), is_foundational=key == 'foundation',
        ) for key, action in (('foundation', 'study:z-foundation'), ('core', 'study:a-core'))),
    )


def constraints(**changes):
    return PlanningConstraints(
        target_by=REFERENCE_TIME + timedelta(days=1), available_hours_per_week=4, **changes,
    )


async def run_product_worker(*, snapshot=None, capabilities=(), availability=(), **preferences):
    snapshot = snapshot or product_snapshot()
    context = replace(
        _context(capabilities=capabilities),
        snapshot_selection=PublishedSnapshotSelection(
            occupation_id=snapshot.occupation_id, basis_version=snapshot.basis_version,
            release_id=snapshot.release.release_id,
        ),
        constraints=constraints(**preferences),
        calendar=tuple(PlanningSlot(
            starts_at=REFERENCE_TIME + timedelta(hours=index),
            ends_at=REFERENCE_TIME + timedelta(hours=index + 1), capacity_week_key='2026-W39',
        ) for index in range(4)),
        candidate_availability=availability,
    )
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(StaticContextReader(context), StaticSnapshotReader(snapshot), finalizer)
    await worker.process(_work_item())
    return finalizer.artifacts[0]


def test_runtime_product_policy_leaves_source_snapshot_and_approved_semantics_immutable():
    snapshot = product_snapshot()
    catalog = product_planning_catalog(snapshot, constraints(needs_portfolio=True, career_switch=True))
    assert catalog.policy_version == PRODUCT_PLANNING_POLICY_VERSION
    assert all(isinstance(template.cost, UnknownKrwCost) for template in snapshot.templates)
    assert all(isinstance(template.cost, KnownKrwCost) and template.cost.krw == 0
               for template in catalog.templates)
    assert snapshot.templates[0].title == 'foundation 학습'
    assert catalog.templates[0].title == 'foundation 포트폴리오 프로젝트'
    assert snapshot.templates[0].support_refs == catalog.templates[0].support_refs
    assert snapshot.templates[0].outcome_requirement_keys == catalog.templates[0].outcome_requirement_keys
    assert snapshot.templates[0].estimated_hours == catalog.templates[0].estimated_hours
    assert len(catalog.decisions) == 3


def test_runtime_policy_does_not_change_nonproduct_catalogs_or_unknown_cost_rules():
    snapshot = _snapshot()
    catalog = product_planning_catalog(snapshot, constraints(needs_portfolio=True, career_switch=True))
    assert catalog.templates is snapshot.templates
    assert catalog.policy_version is None
    assert catalog.decisions == ()


@pytest.mark.anyio
@pytest.mark.parametrize('budget', [None, 0, 1_000_000])
async def test_generated_product_routes_remain_feasible_under_any_nonnegative_budget(budget):
    artifacts = await run_product_worker(max_out_of_pocket_krw=budget)
    proposal = artifacts.proposal
    assert proposal is not None
    assert proposal.feasibility == 'FEASIBLE'
    assert {step['action_id'] for step in proposal.steps} == {'study:z-foundation', 'study:a-core'}
    assert proposal.trace_outputs['rejections'] == ()
    assert proposal.trace_versions['planning_policy_version'] == PRODUCT_PLANNING_POLICY_VERSION
    policy = proposal.trace_outputs['planning_policy']
    assert any('Pricing is not modeled' in decision for decision in policy['decisions'])
    assert artifacts.analysis.input_snapshot['planning_policy'] == policy
    assert proposal.release_id == f'product-roles-v1-{"a" * 64}'


@pytest.mark.anyio
async def test_portfolio_preference_changes_selected_activities_and_records_deliverables():
    regular = await run_product_worker()
    preferred = await run_product_worker(needs_portfolio=True, max_out_of_pocket_krw=0)
    regular_steps, project_steps = regular.proposal.steps, preferred.proposal.steps
    assert {step['action_id'] for step in project_steps} == {
        f'portfolio:{step["action_id"]}' for step in regular_steps
    }
    assert {tuple(step['outcome_requirement_keys']) for step in project_steps} == {
        tuple(step['outcome_requirement_keys']) for step in regular_steps
    }
    assert sum(step['estimated_hours'] for step in regular_steps) == sum(
        step['estimated_hours'] for step in project_steps
    )
    assert all('포트폴리오 프로젝트' in step['title'] for step in project_steps)
    assert all(any('README' in criterion for criterion in step['completion_criteria'])
               for step in project_steps)
    assert all('PORTFOLIO_PREFERENCE' in step['reason_codes'] for step in project_steps)
    assert all(step['outcomes'] for step in project_steps)
    assert preferred.proposal.constraints_snapshot['needs_portfolio'] is True


def test_project_format_preserves_reviewed_prerequisite_graph():
    snapshot = product_snapshot(prerequisite=True)
    catalog = product_planning_catalog(snapshot, constraints(needs_portfolio=True))
    assert catalog.templates[1].prerequisite_action_ids == ('portfolio:study:z-foundation',)
    assert catalog.templates[0].prerequisite_action_ids == ()


@pytest.mark.anyio
async def test_career_switch_prioritizes_unmet_foundations_without_inventing_dependencies():
    regular = await run_product_worker()
    artifacts = await run_product_worker(career_switch=True)
    proposal = artifacts.proposal
    assert [step['action_id'] for step in proposal.steps] == ['study:z-foundation', 'study:a-core']
    foundation, core = proposal.steps
    regular_foundation = next(step for step in regular.proposal.steps
                              if step['action_id'] == 'study:z-foundation')
    assert foundation['planned_start_at'] < regular_foundation['planned_start_at']
    assert foundation['planned_end_at'] <= core['planned_start_at']
    assert foundation['prerequisite_step_keys'] == core['prerequisite_step_keys'] == ()
    assert 'FOUNDATIONAL_PREFERENCE' in foundation['reason_codes']
    assert 'FOUNDATION_PRIORITY_V1' in proposal.trace_versions['objective_version']
    policy = proposal.trace_outputs['planning_policy']
    assert policy['foundational_priority_action_ids'] == ('study:z-foundation',)
    assert any('PRIORITIZE_UNMET_FOUNDATIONS' in decision for decision in policy['decisions'])


@pytest.mark.anyio
async def test_foundational_preference_does_not_repeat_already_satisfied_skills():
    artifacts = await run_product_worker(
        snapshot=product_snapshot(prerequisite=True), career_switch=True, needs_portfolio=True,
        capabilities=(CapabilityInput(raw_text='foundation'),),
    )
    proposal = artifacts.proposal
    assert [step['action_id'] for step in proposal.steps] == ['portfolio:study:a-core']
    assert proposal.steps[0]['prerequisite_step_keys'] == ()
    assert proposal.trace_outputs['planning_policy']['foundational_priority_action_ids'] == ()
    assert 'FOUNDATIONAL_PREFERENCE' not in proposal.steps[0]['reason_codes']


@pytest.mark.anyio
async def test_real_prerequisites_take_precedence_over_foundational_preference():
    snapshot = product_snapshot()
    # Foundation is unusually dependent on a core activity in this reviewed synthetic source.
    # Preference must never reverse the actual dependency, even when its labels suggest otherwise.
    first, second = snapshot.templates
    snapshot = replace(snapshot, templates=(replace(
        first, prerequisite_action_ids=(second.action_id,),
    ), second))
    artifacts = await run_product_worker(snapshot=snapshot, career_switch=True)
    assert [step['action_id'] for step in artifacts.proposal.steps] == ['study:a-core', 'study:z-foundation']
    assert artifacts.proposal.steps[1]['prerequisite_step_keys'] == ('study:a-core@1',)


@pytest.mark.anyio
async def test_portfolio_projects_keep_the_original_activity_availability_window():
    artifacts = await run_product_worker(needs_portfolio=True, availability=(CandidateAvailability(
        action_id='study:z-foundation', available_from=REFERENCE_TIME + timedelta(hours=2),
        available_until=REFERENCE_TIME + timedelta(hours=4),
    ),))
    project = next(step for step in artifacts.proposal.steps
                   if step['action_id'] == 'portfolio:study:z-foundation')
    assert project['planned_start_at'] == (REFERENCE_TIME + timedelta(hours=2)).isoformat()
