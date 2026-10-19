from dataclasses import replace

from jobtology_be.modules.analyses.editorial_models import (
    EditorialAnalysis,
    RequirementNecessity,
    SatisfactionStatus,
)
from jobtology_be.planning.candidate_models import CandidateSet
from jobtology_be.planning.solver_models import PlanningProblem
from jobtology_be.product_roles.planning import RuntimePlanningCatalog
from jobtology_be.workers.context import RecomputeContext


def build_planning_problem(
    context: RecomputeContext,
    analysis: EditorialAnalysis,
    candidate_set: CandidateSet,
    runtime_catalog: RuntimePlanningCatalog | None = None,
) -> PlanningProblem:
    unmet = frozenset(item.requirement_key for item in analysis.requirements
                      if item.status is SatisfactionStatus.UNMET)
    action_ids = dict(runtime_catalog.action_id_mapping) if runtime_catalog else {}
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
        candidate_availability=tuple(replace(item, action_id=action_ids.get(item.action_id, item.action_id))
                                     for item in context.candidate_availability),
        settings=context.solver_settings,
        planning_policy_version=runtime_catalog.policy_version if runtime_catalog else None,
        planning_policy_decisions=runtime_catalog.decisions if runtime_catalog else (),
        foundational_priority_action_ids=frozenset(
            candidate.action_id for candidate in candidate_set.candidates
            if context.constraints.career_switch and runtime_catalog is not None
            and candidate.action_id in runtime_catalog.foundational_action_ids
            and candidate.outcome_requirement_keys & unmet
        ),
        portfolio_action_ids=runtime_catalog.portfolio_action_ids if runtime_catalog else frozenset(),
    )
