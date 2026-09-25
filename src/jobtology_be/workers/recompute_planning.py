from jobtology_be.modules.analyses.editorial_models import (
    EditorialAnalysis,
    RequirementNecessity,
    SatisfactionStatus,
)
from jobtology_be.planning.candidate_models import CandidateSet
from jobtology_be.planning.solver_models import PlanningProblem
from jobtology_be.workers.context import RecomputeContext


def build_planning_problem(
    context: RecomputeContext,
    analysis: EditorialAnalysis,
    candidate_set: CandidateSet,
) -> PlanningProblem:
    return PlanningProblem(
        analysis_id=analysis.analysis_id,
        profile_version=analysis.profile_version,
        basis_version=analysis.basis_version,
        corpus_release_id=analysis.corpus_release_id,
        is_fixture=analysis.is_fixture,
        reference_at=analysis.reference_at,
        candidates=candidate_set.candidates,
        candidate_blockers=candidate_set.blockers,
        required_requirement_keys=frozenset(
            item.requirement_key
            for item in analysis.requirements
            if (
                item.necessity is RequirementNecessity.REQUIRED
                and item.status is not SatisfactionStatus.SATISFIED
            )
        ),
        preferred_requirement_keys=frozenset(
            item.requirement_key
            for item in analysis.requirements
            if (
                item.necessity is RequirementNecessity.PREFERRED
                and item.status is not SatisfactionStatus.SATISFIED
            )
        ),
        constraints=context.constraints,
        planning_started_at=context.planning_started_at,
        calendar=context.calendar,
        candidate_availability=context.candidate_availability,
        settings=context.solver_settings,
    )
