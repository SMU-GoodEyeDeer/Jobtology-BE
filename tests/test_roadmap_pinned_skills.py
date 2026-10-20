from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.api.roadmaps import router
from jobtology_be.application.queries import AnalysisView, RoadmapView

USER, ROADMAP, ORIGIN, GOAL, PROPOSAL = (UUID(int=value) for value in range(1, 6))


class Queries:
    async def get_roadmap(self, user_id, roadmap_id):
        assert user_id == USER and roadmap_id == ROADMAP
        return RoadmapView(ROADMAP, 1, 'ACTIVE', GOAL, PROPOSAL, 'Saved plan', 1,
                           'origin-release', {}, (), analysis_id=ORIGIN)

    async def get_analysis(self, user_id, analysis_id):
        # Current dashboard analysis intentionally has no role in this read.
        assert user_id == USER and analysis_id == ORIGIN
        return AnalysisView(ORIGIN, GOAL, 1, 'EDITORIAL', 'basis', 'origin-release', 'method',
                            'READY', datetime(2026, 10, 20, tzinfo=UTC), {'requirements': [
                                {'requirement_key': 'complete', 'label': 'Completed skill',
                                 'necessity': 'REQUIRED', 'status': 'SATISFIED', 'credit': 1.0,
                                 'base_estimated_hours': 20, 'estimated_hours': 0,
                                 'metadata': {'ncs_level': 4, 'demand_pct': 50, 'estimated_hours': 20,
                                              'hours_basis': 'OFFICIAL', 'qualifications': [
                                                  {'code': 'Q1', 'name': 'Source credential',
                                                   'display_name': 'Source credential', 'kind': 'NATIONAL_TECHNICAL'}]}},
                                {'requirement_key': 'unmet', 'label': 'Remaining skill',
                                 'necessity': 'PREFERRED', 'status': 'UNMET', 'metadata': None},
                            ]})


def test_roadmap_skill_catalog_uses_origin_analysis_and_includes_completed_metadata():
    app = FastAPI()
    app.include_router(router)
    async def principal():
        return AuthenticatedPrincipal(user_id=USER)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = Queries
    with TestClient(app) as client:
        response = client.get(f'/roadmaps/{ROADMAP}/skills')
    assert response.status_code == 200
    body = response.json()
    assert body['analysis_id'] == str(ORIGIN) and body['release_id'] == 'origin-release'
    complete, unmet = body['skills']
    assert complete['status'] == 'SATISFIED' and complete['estimated_hours'] == 0
    assert complete['ncs_level'] == 4 and complete['demand_pct'] == 50
    assert complete['qualifications'][0]['code'] == 'Q1'
    assert unmet['ncs_level'] is unmet['demand_pct'] is None


def test_historical_completed_metadata_stays_with_its_original_step_and_release():
    from dataclasses import replace

    from jobtology_be.application.queries import RoadmapStepView

    historical_step = UUID(int=20)
    historical_analysis = UUID(int=21)
    class HistoryQueries(Queries):
        async def get_roadmap(self, user_id, roadmap_id):
            roadmap = await super().get_roadmap(user_id, roadmap_id)
            history = {'completed:old': {
                'source_analysis_id': str(historical_analysis), 'source_release_id': 'older-release',
                'source_requirements': [{'requirement_key': 'complete', 'label': 'Earlier completed skill',
                                          'necessity': 'REQUIRED', 'status': 'UNMET', 'base_estimated_hours': 8,
                                          'metadata': {'ncs_level': 2, 'demand_pct': 10, 'estimated_hours': 8,
                                                       'hours_basis': 'OFFICIAL'}}],
            }}
            step = RoadmapStepView(historical_step, 'completed:old', 0, 'study:old', 1,
                                   'COMPLETED', None, None, ({'requirement_key': 'complete'},), (), (), 'Old lesson')
            return replace(roadmap, validity={'revision_history_steps': history}, steps=(step,))

    app = FastAPI()
    app.include_router(router)
    async def principal():
        return AuthenticatedPrincipal(user_id=USER)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = HistoryQueries
    with TestClient(app) as client:
        response = client.get(f'/roadmaps/{ROADMAP}/skills')
    assert response.status_code == 200
    body = response.json()
    current = next(item for item in body['skills'] if item['source_analysis_id'] == str(ORIGIN)
                   and item['requirement_key'] == 'complete')
    historical = next(item for item in body['skills'] if str(historical_step) in item['step_ids'])
    assert current['ncs_level'] == 4 and current['step_ids'] == []
    assert historical['source_analysis_id'] == str(historical_analysis)
    assert historical['ncs_level'] == 2 and historical['demand_pct'] == 10
    assert historical['status'] == 'SATISFIED' and historical['estimated_hours'] == 0
