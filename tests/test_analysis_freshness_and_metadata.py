from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import TypeAdapter
from test_analysis_context_factory import _context_document
from test_recompute_worker import (
    RecordingFinalizer,
    StaticContextReader,
    StaticSnapshotReader,
    _context,
    _snapshot,
    _work_item,
)

from jobtology_be.api.analyses import _analysis_response
from jobtology_be.application.queries import AnalysisView
from jobtology_be.application.recompute_freshness import analysis_context_fingerprint
from jobtology_be.application.requirement_metadata import (
    RequirementMetadata,
    RequirementQualification,
)
from jobtology_be.workers.recompute import RecomputeWorker

META = RequirementMetadata(
    ncs_level=4, demand_pct=60, estimated_hours=1, hours_basis='OFFICIAL',
    demand_postings=6, demand_base=10, ncs_base_code='2001020211',
    qualifications=(RequirementQualification('Q1', '자격', '자격', 'NATIONAL_TECHNICAL'),),
)


class Metadata:
    def __init__(self, release_id='release-old'):
        self.release_id = release_id

    def lookup(self, key):
        return replace(META, ncs_level=7, demand_pct=90, estimated_hours=50)


def response(requirement, lookup):
    view = AnalysisView(UUID(int=1), UUID(int=2), 1, 'EDITORIAL', 'v1', 'release-old',
                        'method', 'READY', datetime(2026, 10, 20, tzinfo=UTC),
                        {'requirements': [dict(requirement_key='api', label='API', necessity='REQUIRED',
                                               status='UNMET', **requirement)]})
    return _analysis_response(view, lookup).result.skills[0]


def test_analysis_metadata_is_frozen_and_legacy_fallback_requires_exact_release():
    frozen = response({'metadata': TypeAdapter(RequirementMetadata).dump_python(META, mode='json'), 'base_estimated_hours': 1, 'estimated_hours': 1}, Metadata())
    assert (frozen.ncs_level, frozen.demand_pct, frozen.estimated_hours) == (4, 60, 1)
    assert frozen.qualifications[0].code == 'Q1'
    legacy_same_release = response({}, Metadata())
    assert legacy_same_release.demand_pct == 90
    legacy_other_release = response({}, Metadata('release-new'))
    assert legacy_other_release.ncs_level is legacy_other_release.demand_pct is None
    assert legacy_other_release.estimated_hours is None
    explicitly_unknown = response({'metadata': None}, Metadata())
    assert explicitly_unknown.ncs_level is None


def test_context_fingerprint_ignores_clock_jitter_but_changes_when_inputs_expire_or_calendar_changes():
    original = _context_document().model_dump(mode='json')
    jitter = dict(original, reference_at='2026-09-22T13:00:00+00:00',
                  planning_started_at='2026-09-22T13:00:00+00:00')
    assert analysis_context_fingerprint(original) == analysis_context_fingerprint(jitter)
    for changed in (
        dict(original, reference_at='2026-09-23T12:00:00+00:00'),
        dict(original, snapshot_selection=dict(original['snapshot_selection'], release_id='new-release')),
        dict(original, capabilities=[{'raw_text': 'expired certification', 'credit': 0.0}]),
        dict(original, calendar=[{'capacity_week_key': '2026-W39'}]),
        dict(original, constraints=dict(original['constraints'], available_hours_per_week=20)),
    ):
        assert analysis_context_fingerprint(original) != analysis_context_fingerprint(changed)


@pytest.mark.anyio
async def test_worker_freezes_metadata_hourly_allocations_and_complete_solver_telemetry():
    finalizer = RecordingFinalizer()
    worker = RecomputeWorker(
        StaticContextReader(replace(_context(), requirement_metadata={'api': META})),
        StaticSnapshotReader(_snapshot()), finalizer,
    )
    await worker.process(_work_item())
    artifacts = finalizer.artifacts[0]
    assert artifacts.analysis.results['requirements'][0]['metadata']['demand_pct'] == 60
    assert artifacts.analysis.results['requirements'][0]['metadata']['qualifications'][0]['code'] == 'Q1'
    allocations = artifacts.proposal.steps[0]['slot_allocations']
    assert len(allocations) == artifacts.proposal.steps[0]['estimated_hours'] == 1
    assert datetime.fromisoformat(allocations[0]['ends_at']) - datetime.fromisoformat(allocations[0]['starts_at']) == timedelta(hours=1)
    solver = artifacts.proposal.trace_outputs['solver']
    assert solver['solver_worker_count'] == 1 and solver['solver_seed'] > 0
    assert solver['objective']['version']
    assert solver['solver_runs'][0]['phase'] == 'FULL'
    assert solver['solver_runs'][0]['status'] == 'OPTIMAL'
    assert solver['solver_runs'][0]['objective_value'] == solver['solver_runs'][0]['best_objective_bound']


def test_explicit_calendar_changes_are_not_confused_with_flexible_clock_jitter():
    original = _context_document().model_dump(mode='json')
    original['calendar'] = [{'capacity_week_key': '2026-W39', 'starts_at': '2026-09-22T12:00:00+00:00',
                             'ends_at': '2026-09-22T13:00:00+00:00'}]
    later = dict(original, calendar=[{'capacity_week_key': '2026-W39', 'starts_at': '2026-09-22T14:00:00+00:00',
                                      'ends_at': '2026-09-22T15:00:00+00:00'}])
    assert analysis_context_fingerprint(original) != analysis_context_fingerprint(later)
    original['calendar_policy'] = later['calendar_policy'] = 'FLEXIBLE_WEEKLY_V1'
    assert analysis_context_fingerprint(original) == analysis_context_fingerprint(later)


def test_reference_day_identity_is_canonical_across_timezone_offsets():
    original = _context_document().model_dump(mode='json')
    same_instant = dict(original, reference_at='2026-09-23T01:00:00+13:00',
                        planning_started_at='2026-09-23T01:00:00+13:00')
    assert analysis_context_fingerprint(original) == analysis_context_fingerprint(same_instant)
