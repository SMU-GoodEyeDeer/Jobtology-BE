"""Explicit development fixtures, not computed or persisted product results."""

from fastapi import APIRouter

from jobtology_be.contracts import (
    AnalysisPreview,
    ProposedStep,
    RequirementResult,
    RoadmapPreview,
)

router = APIRouter(tags=["development fixtures"])


@router.get(
    "/analysis",
    response_model=AnalysisPreview,
    summary="Get development analysis fixture",
    description="Returns a read-only illustrative fixture when explicitly enabled; it is not product data.",
)
def analysis_fixture() -> AnalysisPreview:
    return AnalysisPreview(
        analysis_id="fixture-analysis",
        profile_version=1,
        basis_type="EDITORIAL",
        basis_version="fixture-v1",
        corpus_release_id=None,
        analysis_status="READY",
        requirements=[
            RequirementResult(
                requirement_key="api-implementation",
                label="API 구현 경험",
                currently_satisfied=False,
                support_refs=["fixture-template:api-project:v1"],
            )
        ],
        matched_count=0,
        total_count=1,
        is_fixture=True,
    )


@router.get(
    "/route-proposal",
    response_model=RoadmapPreview,
    summary="Get development route-proposal fixture",
    description="Returns a read-only illustrative fixture when explicitly enabled; it is not product data.",
)
def proposal_fixture() -> RoadmapPreview:
    return RoadmapPreview(
        route_proposal_id="fixture-proposal",
        analysis_id="fixture-analysis",
        profile_version=1,
        basis_version="fixture-v1",
        feasibility="FEASIBLE",
        proposed_steps=[
            ProposedStep(
                step_key="fixture-step",
                action_id="api-project",
                template_version=1,
                title="API 프로젝트 구현 (개발용 예시)",
                estimated_hours=40,
                prerequisite_step_keys=[],
                outcome_requirement_keys=["api-implementation"],
                completion_criteria=["핵심 API 구현", "실행 방법 문서화"],
                reason_codes=["MISSING_REQUIRED_CAPABILITY"],
            )
        ],
        is_fixture=True,
    )
