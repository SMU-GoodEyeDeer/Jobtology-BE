from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_product_role_planning import product_snapshot
from test_recompute_worker import (
    REFERENCE_TIME,
    RecordingFinalizer,
    StaticContextReader,
    StaticSnapshotReader,
    _context,
    _context_payload,
    _snapshot,
    _work_item,
)

from jobtology_be.api.analyses import AnalysisResultResponse
from jobtology_be.application.queries import RoadmapStepView
from jobtology_be.application.requirement_metadata import (
    RequirementMetadata,
    build_analysis_summary,
)
from jobtology_be.application.weekly_plan import build_weekly_plan
from jobtology_be.application.weekly_plan_models import PlanContext, PlanStepInput
from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedSnapshotSelection
from jobtology_be.modules.analyses.editorial import EditorialGapAnalyzer
from jobtology_be.modules.analyses.editorial_models import (
    EditorialAnalysisRequest,
    InputCompleteness,
)
from jobtology_be.modules.profiles.normalizer import CatalogProfileNormalizer
from jobtology_be.planning.calendar import flexible_weekly_slots
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.effort import remaining_learning_hours
from jobtology_be.product_roles.planning import (
    PRODUCT_PLANNING_POLICY_VERSION,
    product_planning_catalog,
)
from jobtology_be.workers.context import RecomputeContextDocument
from jobtology_be.workers.recompute import RecomputeWorker


@pytest.mark.parametrize('credit', [-0.1, 1.1, float('nan'), float('inf'), True, '0.5'])
def test_internal_credit_contract_rejects_unbounded_or_untyped_values(credit):
    with pytest.raises(ValidationError):
        CapabilityInput(raw_text='foundation', credit=credit)


def test_normalizer_preserves_derived_credit_without_promoting_it_to_verified_evidence():
    snapshot = product_snapshot()
    normalized = CatalogProfileNormalizer(snapshot.capability_entries, snapshot.allowed_experience_codes).normalize(
        'user', 1, [CapabilityInput(raw_text='foundation', credit=.5)],
    )
    assert normalized.capabilities[0].credit == .5
    assert normalized.capabilities[0].verification == 'SELF_REPORTED'


async def partial_artifacts(credit, *, duplicate_credit=None):
    snapshot = product_snapshot()
    snapshot = replace(snapshot, templates=tuple(replace(template, estimated_hours=20)
                                                 for template in snapshot.templates))
    capabilities = [CapabilityInput(raw_text='foundation', credit=credit), CapabilityInput(raw_text='core')]
    if duplicate_credit is not None:
        capabilities += [CapabilityInput(raw_text='foundation', credit=duplicate_credit)] * 2
    constraints = PlanningConstraints(target_by=REFERENCE_TIME + timedelta(days=7), available_hours_per_week=30)
    context = replace(
        _context(capabilities=tuple(capabilities)),
        snapshot_selection=PublishedSnapshotSelection(
            occupation_id=snapshot.occupation_id, basis_version=snapshot.basis_version,
            release_id=snapshot.release.release_id,
        ), constraints=constraints,
        calendar=flexible_weekly_slots(REFERENCE_TIME, constraints.target_by, 30),
    )
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(StaticContextReader(context), StaticSnapshotReader(snapshot), finalizer)
    await worker.process(_work_item())
    return snapshot, context, finalizer.artifacts[0]


@pytest.mark.anyio
@pytest.mark.parametrize('credit,expected_hours', [(0.0, 20), (.25, 15), (.5, 10), (.75, 5)])
async def test_partial_credit_changes_actual_solver_proposal_analysis_and_weekly_effort(credit, expected_hours):
    snapshot, context, artifacts = await partial_artifacts(credit)
    proposal = artifacts.proposal
    assert proposal.feasibility == 'FEASIBLE'
    assert len(proposal.steps) == 1
    step = proposal.steps[0]
    assert step['estimated_hours'] == expected_hours
    assert step['outcome_requirement_keys'] == ('foundation',)
    # The real CP-SAT model used the reduced duration, rather than merely changing a label.
    assert datetime.fromisoformat(step['planned_end_at']) - datetime.fromisoformat(step['planned_start_at']) == timedelta(hours=expected_hours)
    assert all(template.estimated_hours == 20 for template in snapshot.templates)
    assert proposal.trace_versions['planning_policy_version'] == PRODUCT_PLANNING_POLICY_VERSION
    requirement = next(item for item in artifacts.analysis.results['requirements']
                       if item['requirement_key'] == 'foundation')
    assert requirement['credit'] == credit
    assert requirement['base_estimated_hours'] == 20
    assert requirement['estimated_hours'] == expected_hours
    assert requirement['status'] == ('PARTIAL' if credit else 'UNMET')
    summary = build_analysis_summary(artifacts.analysis.results, None)
    assert summary.required_pct == 50  # Core is fully satisfied; partial credit is separate.
    assert summary.weighted_required_pct == round((1 + credit) / 2 * 100)
    assert summary.skills[0].credit == credit
    assert summary.skills[0].estimated_hours == expected_hours
    assert summary.skills[0].base_estimated_hours == 20
    response = AnalysisResultResponse.model_validate(summary, from_attributes=True).model_dump()
    assert response['skills'][0]['status'] == ('PARTIAL' if credit else 'UNMET')
    assert response['skills'][0]['credit'] == credit
    assert response['skills'][0]['estimated_hours'] == expected_hours
    assert response['weighted_score_method'] == 'MODELED_COMPETENCY_CREDIT_V1'
    if credit:
        decisions = proposal.trace_outputs['planning_policy']['decisions']
        assert any(f'remaining_hours={expected_hours}' in decision for decision in decisions)
    roadmap_step = RoadmapStepView(
        step_id=uuid4(), step_key=step['step_key'], position=0, action_id=step['action_id'],
        template_revision=step['template_revision'], state='TODO',
        planned_start=datetime.fromisoformat(step['planned_start_at']),
        planned_end=datetime.fromisoformat(step['planned_end_at']),
        outcomes=step['outcomes'], criteria=step['completion_criteria'], prerequisite_step_ids=(),
        title=step['title'],
    )
    plan = build_weekly_plan(PlanContext(
        roadmap_id=uuid4(), created_at=context.reference_at, recommended_weekly_hours=30,
        step_hours={step['step_key']: step['estimated_hours']},
    ), (PlanStepInput(roadmap_step, RequirementMetadata(
        ncs_level=2, demand_pct=None, estimated_hours=20, hours_basis='OFFICIAL',
    )),))
    assert plan.total_hours == plan.steps[0].hours == plan.steps[0].recommended_hours == expected_hours
    assert plan.steps[0].hours_basis == ('ESTIMATED' if credit else 'OFFICIAL')


@pytest.mark.anyio
async def test_duplicate_partial_evidence_uses_best_credit_and_full_credit_removes_requirement():
    _, _, duplicate = await partial_artifacts(.25, duplicate_credit=.75)
    assert duplicate.proposal.steps[0]['estimated_hours'] == 5
    _, _, fully_satisfied = await partial_artifacts(1.0)
    assert fully_satisfied.proposal.steps == ()
    assert all(item['status'] == 'SATISFIED' for item in fully_satisfied.analysis.results['requirements'])
    assert build_analysis_summary(fully_satisfied.analysis.results, None).skills == ()


def test_context_document_round_trip_preserves_partial_credit():
    payload = _context_payload()
    payload['capabilities'][0]['credit'] = .25
    document = RecomputeContextDocument.model_validate(payload)
    replay = RecomputeContextDocument.model_validate_json(document.model_dump_json()).to_context()
    assert replay.capabilities[0].credit == .25


@pytest.mark.parametrize('base,credit,expected', [(1, .75, 1), (3, .25, 3), (3, .5, 2), (3, .75, 1), (20, 1, 0)])
def test_remaining_effort_rounds_up_without_inventing_zero_hour_partial_activities(base, credit, expected):
    assert remaining_learning_hours(base, credit) == expected


def test_multi_outcome_activity_uses_the_least_credited_outcome_conservatively():
    snapshot = product_snapshot()
    template = replace(snapshot.templates[0], estimated_hours=20,
                       outcome_requirement_keys=frozenset({'foundation', 'core'}))
    snapshot = replace(snapshot, templates=(template,))
    profile = CatalogProfileNormalizer(snapshot.capability_entries, snapshot.allowed_experience_codes).normalize(
        'user', 1, [CapabilityInput(raw_text='foundation', credit=.75)],
    )
    analysis = EditorialGapAnalyzer(snapshot.editorial_baseline()).evaluate(profile, EditorialAnalysisRequest(
        analysis_id=str(uuid4()), occupation_id=snapshot.occupation_id, basis_version=snapshot.basis_version,
        reference_at=REFERENCE_TIME,
    ), InputCompleteness(entities_complete=True))
    catalog = product_planning_catalog(snapshot, PlanningConstraints(
        target_by=REFERENCE_TIME + timedelta(days=7), available_hours_per_week=30,
    ), analysis)
    # One familiar outcome cannot remove the work needed for another unmodeled outcome.
    assert catalog.templates[0].estimated_hours == 20
    assert template.estimated_hours == 20


@pytest.mark.anyio
async def test_global_specs_outside_the_goal_are_preserved_without_failing_or_receiving_alias_credit():
    snapshot = _snapshot()
    foreign = CapabilityInput(raw_text='API implementation', entity_id='ncs:unit:2001070306',
                              experience_codes=['DELIVERED'], credit=1.0)
    unmapped = CapabilityInput(raw_text='API implementation', experience_codes=['DELIVERED'], credit=0.0)
    context = _context(capabilities=(foreign, unmapped))
    finalizer = RecordingFinalizer()
    await RecomputeWorker(StaticContextReader(context), StaticSnapshotReader(snapshot), finalizer).process(_work_item())
    artifacts = finalizer.artifacts[0]
    assert artifacts.analysis.status == 'READY'
    requirement = artifacts.analysis.results['requirements'][0]
    assert requirement['status'] == 'UNMET'
    assert requirement['credit'] == 0.0
    assert artifacts.proposal.steps[0]['estimated_hours'] == 1
    assert context.capabilities == (foreign, unmapped)
    assert artifacts.analysis.input_snapshot['profile']['capabilities'][0]['entity_id'] == foreign.entity_id
