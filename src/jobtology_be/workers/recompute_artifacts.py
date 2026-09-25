import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedSnapshotSelection
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisPersist,
    JsonValue,
    RecomputeArtifacts,
    RecomputeWorkItem,
    RouteProposalPersist,
)
from jobtology_be.modules.analyses.editorial_models import (
    Coverage,
    EditorialAnalysis,
    EditorialBaseline,
)
from jobtology_be.planning.candidate_models import ActivityTemplate
from jobtology_be.planning.contracts import PlanningConstraints
from jobtology_be.planning.solver_models import (
    PlanningConstraintSnapshot,
    PlanningResult,
    ScheduledRouteStep,
)
from jobtology_be.workers.outcomes import PinnedOutcomeResolver


class ArtifactContext(Protocol):
    @property
    def user_id(self) -> UUID: ...

    @property
    def profile_version(self) -> int: ...

    @property
    def goal_id(self) -> UUID: ...

    @property
    def snapshot_selection(self) -> PublishedSnapshotSelection: ...

    @property
    def capabilities(self) -> tuple[CapabilityInput, ...]: ...

    @property
    def constraints(self) -> PlanningConstraints: ...

    @property
    def reference_at(self) -> datetime: ...

    @property
    def planning_started_at(self) -> datetime: ...


def build_recompute_artifacts(
    *,
    context: ArtifactContext,
    work_item: RecomputeWorkItem,
    analysis: EditorialAnalysis,
    baseline: EditorialBaseline,
    templates: tuple[ActivityTemplate, ...],
    planning_result: PlanningResult,
) -> RecomputeArtifacts:
    input_snapshot = _input_snapshot(context)
    analysis_id = UUID(analysis.analysis_id)
    proposal = _proposal(
        user_id=work_item.user_id,
        analysis_id=analysis_id,
        profile_version=analysis.profile_version,
        outcome_resolver=PinnedOutcomeResolver(
            requirements=baseline.requirements,
            templates=templates,
        ),
        planning_result=planning_result,
    )
    return RecomputeArtifacts(
        analysis=AnalysisPersist(
            user_id=work_item.user_id,
            analysis_id=analysis_id,
            goal_id=context.goal_id,
            profile_version=analysis.profile_version,
            basis_type="EDITORIAL",
            basis_version=analysis.basis_version,
            release_id=analysis.corpus_release_id,
            methodology_version="EDITORIAL_CHECKLIST_V1",
            status=analysis.analysis_status,
            reference_at=analysis.reference_at,
            input_snapshot=input_snapshot,
            input_hash=_hash(input_snapshot),
            results=_analysis_results(analysis, planning_result),
            is_fixture=analysis.is_fixture,
        ),
        proposal=proposal,
    )


def _input_snapshot(context: ArtifactContext) -> Mapping[str, JsonValue]:
    return {
        "profile": {
            "user_id": str(context.user_id),
            "profile_version": context.profile_version,
            "capabilities": tuple(capability.model_dump(mode="json") for capability in context.capabilities),
        },
        "goal": {"goal_id": str(context.goal_id)},
        "preferences": context.constraints.model_dump(mode="json"),
        "snapshot_selection": {
            "occupation_id": context.snapshot_selection.occupation_id,
            "basis_version": context.snapshot_selection.basis_version,
            "release_id": context.snapshot_selection.release_id,
        },
        "reference_at": context.reference_at.isoformat(),
        "planning_started_at": context.planning_started_at.isoformat(),
    }


def _analysis_results(
    analysis: EditorialAnalysis, planning_result: PlanningResult
) -> Mapping[str, JsonValue]:
    results: dict[str, JsonValue] = {
        "analysis_id": analysis.analysis_id,
        "analysis_status": analysis.analysis_status,
        "basis_version": analysis.basis_version,
        "corpus_release_id": analysis.corpus_release_id,
        "is_fixture": analysis.is_fixture,
        "occupation_id": analysis.occupation_id,
        "preferred_coverage": _coverage(analysis.preferred_coverage),
        "profile_version": analysis.profile_version,
        "reference_at": analysis.reference_at.isoformat(),
        "required_coverage": _coverage(analysis.required_coverage),
        "requirements": tuple(
            {
                "label": item.label,
                "necessity": item.necessity,
                "provenance": tuple(sorted(item.provenance)),
                "requirement_key": item.requirement_key,
                "status": item.status,
                "support_refs": tuple(sorted(item.support_refs)),
            }
            for item in analysis.requirements
        ),
    }
    if planning_result.feasibility is None:
        results["route"] = {
            "feasibility": None,
            "optimization_status": planning_result.optimization_status,
            "trace": _timeout_trace(planning_result),
        }
    return results


def _timeout_trace(planning_result: PlanningResult) -> Mapping[str, JsonValue]:
    trace = planning_result.trace
    return {
        "analysis_id": trace.analysis_id,
        "basis_version": trace.basis_version,
        "corpus_release_id": trace.corpus_release_id,
        "reference_at": trace.reference_at.isoformat(),
        "solver_seed": trace.solver_seed,
        "solver_worker_count": trace.solver_worker_count,
        "template_versions": {
            item.action_id: item.template_revision for item in trace.candidate_versions
        },
        "time_limit_seconds": trace.time_limit_seconds,
    }


def _coverage(coverage: Coverage) -> Mapping[str, JsonValue]:
    return {
        "availability": coverage.availability,
        "matched_count": coverage.matched_count,
        "reason": coverage.reason,
        "score": coverage.score,
        "score_method": coverage.score_method,
        "total_count": coverage.total_count,
    }


def _proposal(
    *,
    user_id: UUID,
    analysis_id: UUID,
    profile_version: int,
    outcome_resolver: PinnedOutcomeResolver,
    planning_result: PlanningResult,
) -> RouteProposalPersist | None:
    if planning_result.feasibility is None:
        return None
    steps = tuple(_step(step, outcome_resolver) for step in planning_result.scheduled_steps)
    trace_versions: Mapping[str, JsonValue] = {
        "basis_version": planning_result.trace.basis_version,
        "corpus_release_id": planning_result.trace.corpus_release_id,
        "objective_version": planning_result.trace.objective.version,
        "template_versions": {
            item.action_id: item.template_revision for item in planning_result.trace.candidate_versions
        },
    }
    trace_outputs: Mapping[str, JsonValue] = {
        "rejections": tuple(
            {
                "action_id": rejection.action_id,
                "code": rejection.code,
                "requirement_key": rejection.requirement_key,
            }
            for rejection in planning_result.trace.rejections
        ),
        "optimization_status": planning_result.optimization_status,
    }
    constraints_snapshot = _constraints_snapshot(planning_result.trace.constraints)
    proposal_hash = _hash({"steps": steps, "trace": trace_outputs})
    return RouteProposalPersist(
        user_id=user_id,
        proposal_id=uuid4(),
        analysis_id=analysis_id,
        profile_version=profile_version,
        proposal_hash=proposal_hash,
        constraints_snapshot=constraints_snapshot,
        feasibility=planning_result.feasibility,
        optimization_status=planning_result.optimization_status,
        steps=steps,
        trace_id=uuid4(),
        trace_versions=trace_versions,
        trace_outputs=trace_outputs,
        release_id=planning_result.trace.corpus_release_id,
    )


def _constraints_snapshot(snapshot: PlanningConstraintSnapshot) -> Mapping[str, JsonValue]:
    return {
        "available_hours_per_week": snapshot.available_hours_per_week,
        "budget_mode": snapshot.budget_mode,
        "career_switch": snapshot.career_switch,
        "fastest_path": snapshot.fastest_path,
        "max_out_of_pocket_krw": snapshot.max_out_of_pocket_krw,
        "needs_portfolio": snapshot.needs_portfolio,
        "target_by": snapshot.target_by.isoformat(),
    }


def _step(
    step: ScheduledRouteStep, outcome_resolver: PinnedOutcomeResolver
) -> Mapping[str, JsonValue]:
    return {
        "action_id": step.action_id,
        "completion_criteria": step.completion_criteria,
        "estimated_hours": step.estimated_hours,
        "outcomes": outcome_resolver.serialize(step),
        "outcome_requirement_keys": step.outcome_requirement_keys,
        "planned_end_at": step.planned_end_at.isoformat(),
        "planned_start_at": step.planned_start_at.isoformat(),
        "prerequisite_step_keys": step.prerequisite_step_keys,
        "reason_codes": step.reason_codes,
        "step_key": step.step_key,
        "support_refs": step.support_refs,
        "template_revision": step.template_revision,
        "title": step.title,
    }


def _hash(value: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
