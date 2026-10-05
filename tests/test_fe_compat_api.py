from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.analyses import require_requirement_metadata
from jobtology_be.api.analyses import router as analyses_router
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.m5_queries import require_occupation_display_names
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.api.roadmaps import router as roadmaps_router
from jobtology_be.application.m5_queries import OccupationView
from jobtology_be.application.queries import (
    AnalysisView,
    RecomputeView,
    RoadmapStepView,
    RoadmapView,
)
from jobtology_be.application.requirement_metadata import RequirementMetadata
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER = UUID('d0b3d8d1-7de4-4a62-8c58-e652431377b4')
ANALYSIS = UUID('d0b3d8d1-7de4-4a62-8c58-e652431377b5')
REQUEST = UUID('d0b3d8d1-7de4-4a62-8c58-e652431377b6')
ROADMAP = UUID('d0b3d8d1-7de4-4a62-8c58-e652431377b7')


class Names:
    def display_name(self, occupation_id: str) -> str | None:
        return '백엔드 개발자' if occupation_id == 'BACKEND_DEVELOPER' else None


class Metadata:
    def lookup(self, requirement_key: str) -> RequirementMetadata | None:
        return {
            'required': RequirementMetadata(ncs_level=4, demand_pct=32,
                                            estimated_hours=40, hours_basis='OFFICIAL'),
            'preferred': RequirementMetadata(ncs_level=6, demand_pct=None,
                                             estimated_hours=20, hours_basis='ESTIMATED'),
        }.get(requirement_key)


class Occupations:
    async def get_occupations(self) -> tuple[OccupationView, ...]:
        return (OccupationView('BACKEND_DEVELOPER', 'v1', 'r1', None),
                OccupationView('UNKNOWN', 'v1', 'r1', None))


class Queries:
    async def get_recompute(self, user_id: UUID, recompute_request_id: UUID) -> RecomputeView:
        assert user_id == USER and recompute_request_id == REQUEST
        return RecomputeView(REQUEST, 2, 'READY', ANALYSIS, None, None)

    async def get_analysis(self, user_id: UUID, analysis_id: UUID) -> AnalysisView:
        assert user_id == USER and analysis_id == ANALYSIS
        results = {
            'required_coverage': {'availability': 'AVAILABLE', 'score': 0.7},
            'preferred_coverage': {'availability': 'UNAVAILABLE', 'score': None},
            'requirements': [
                {'requirement_key': 'preferred', 'label': '고급 분석',
                 'necessity': 'PREFERRED', 'status': 'NEEDS_INPUT'},
                {'requirement_key': 'done', 'label': '완료 역량',
                 'necessity': 'REQUIRED', 'status': 'SATISFIED'},
                {'requirement_key': 'required', 'label': '서버 구현',
                 'necessity': 'REQUIRED', 'status': 'UNMET'},
            ],
        }
        return AnalysisView(ANALYSIS, REQUEST, 2, 'EDITORIAL', 'v1', 'r1',
                            'v1', 'READY', datetime(2026, 10, 5, tzinfo=UTC), results)

    async def list_roadmaps(self, user_id: UUID) -> tuple[RoadmapView, ...]:
        return (await self.get_roadmap(user_id, ROADMAP),)

    async def get_roadmap(self, user_id: UUID, roadmap_id: UUID) -> RoadmapView:
        assert user_id == USER and roadmap_id == ROADMAP
        step = RoadmapStepView(
            step_id=ANALYSIS, step_key='study:sql', position=0, action_id='study:sql',
            template_revision=1, state='TODO', planned_start=None, planned_end=None,
            outcomes=(), criteria=('SQL 실습 완료',), prerequisite_step_ids=(), title='SQL 학습',
        )
        return RoadmapView(ROADMAP, 3, 'ACTIVE', REQUEST, ANALYSIS, '로드맵',
                           2, 'r1', {}, (step,))


class EmptyAnalysisQueries(Queries):
    async def get_analysis(self, user_id: UUID, analysis_id: UUID) -> AnalysisView:
        return AnalysisView(ANALYSIS, REQUEST, 2, 'EDITORIAL', 'v1', 'r1',
                            'v1', 'PENDING', datetime(2026, 10, 5, tzinfo=UTC), None)


async def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id=USER)


def test_occupations_are_public_and_have_display_name_or_fallback() -> None:
    # Given: a published occupation list and role-local display names, without an auth override.
    app = create_app(Settings(enable_fixtures=False),
                     dependencies=ApiDependencies(m5_queries=Occupations()))
    app.dependency_overrides[require_occupation_display_names] = Names

    # When: an unauthenticated user lists occupations.
    with TestClient(app) as client:
        response = client.get('/api/v1/occupations')

    # Then: both occupations are visible and unknown IDs have a safe fallback.
    assert response.status_code == 200
    assert [(item['occupation_id'], item['name']) for item in response.json()] == [
        ('BACKEND_DEVELOPER', '백엔드 개발자'), ('UNKNOWN', 'UNKNOWN')]


def test_recompute_ready_response_has_completed_state_and_analysis_id() -> None:
    # Given: an authenticated READY recompute with a persisted analysis.
    app = FastAPI()
    app.include_router(analyses_router)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = Queries

    # When: the user polls the recomputation.
    with TestClient(app) as client:
        response = client.get(f'/recomputations/{REQUEST}')

    # Then: the FE's completion discriminator and result link are present.
    assert response.status_code == 200
    assert response.json()['state'] == 'COMPLETED'
    assert response.json()['analysis_id'] == str(ANALYSIS)
    assert response.json()['resulting_analysis_id'] == str(ANALYSIS)


def test_analysis_result_projects_unmet_skills_and_metadata() -> None:
    # Given: scored persisted results and approved requirement metadata.
    app = FastAPI()
    app.include_router(analyses_router)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = Queries
    app.dependency_overrides[require_requirement_metadata] = Metadata

    # When: the user retrieves the analysis.
    with TestClient(app) as client:
        response = client.get(f'/analyses/{ANALYSIS}')

    # Then: the FE summary excludes satisfied requirements and labels estimated hours.
    assert response.status_code == 200
    assert response.json()['result'] == {
        'required_pct': 70, 'preferred_pct': None,
        'skills': [
            {'name': '서버 구현', 'type': '필수', 'demand_pct': 32,
             'difficulty': 'NCS 수준 4', 'experienced_pct': None, 'achievement': '학습 40시간'},
            {'name': '고급 분석', 'type': '우대', 'demand_pct': None,
             'difficulty': 'NCS 수준 6', 'experienced_pct': None,
             'achievement': '학습 20시간 (추정)'},
        ],
    }


def test_analysis_without_stored_results_has_null_fe_result() -> None:
    # Given: an analysis record with no persisted results yet.
    app = FastAPI()
    app.include_router(analyses_router)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = EmptyAnalysisQueries

    # When: the user retrieves that analysis.
    with TestClient(app) as client:
        response = client.get(f'/analyses/{ANALYSIS}')

    # Then: the FE result remains null rather than implying progress.
    assert response.status_code == 200
    assert response.json()['result'] is None


def test_analysis_without_metadata_does_not_invent_ncs_values() -> None:
    # Given: persisted analysis results without an approved metadata lookup.
    app = FastAPI()
    app.include_router(analyses_router)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = Queries

    # When: the user reads the result.
    with TestClient(app) as client:
        response = client.get(f'/analyses/{ANALYSIS}')

    # Then: source-derived percentages survive but unknown per-skill facts stay null.
    assert response.status_code == 200
    assert response.json()['result']['required_pct'] == 70
    assert [(item['demand_pct'], item['difficulty'], item['achievement'])
            for item in response.json()['result']['skills']] == [(None, None, None)] * 2


def test_saved_roadmap_includes_status_version_and_step_title_description() -> None:
    # Given: an authenticated saved roadmap with a proposal-derived step title.
    app = FastAPI()
    app.include_router(roadmaps_router)
    app.dependency_overrides[require_authenticated_principal] = principal
    app.dependency_overrides[require_product_queries] = Queries

    # When: the user retrieves list and detail views.
    with TestClient(app) as client:
        detail = client.get(f'/roadmaps/{ROADMAP}')
        listing = client.get('/roadmaps')

    # Then: both shapes carry FE aliases and a human-readable step.
    assert detail.status_code == listing.status_code == 200
    for roadmap in (detail.json(), listing.json()['items'][0]):
        assert roadmap['status'] == 'ACTIVE' and roadmap['version'] == 3
        assert roadmap['steps'][0]['title'] == 'SQL 학습'
        assert roadmap['steps'][0]['description'] == 'SQL 실습 완료'
