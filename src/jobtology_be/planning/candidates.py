from dataclasses import dataclass
from typing import override

from jobtology_be.modules.analyses.editorial_models import (
    AnalysisStatus,
    EditorialAnalysis,
    RequirementEvaluation,
    SatisfactionStatus,
)
from jobtology_be.planning.candidate_models import (
    ActivityTemplate,
    Candidate,
    CandidateSet,
    InsufficientAnalysisBlocker,
    NeedsInputBlocker,
    NoActivitiesBlocker,
)


@dataclass(slots=True)
class DuplicateTemplateRevisionError(Exception):
    action_id: str
    revision: int

    @override
    def __str__(self) -> str:
        return f"duplicate activity template revision: {self.action_id}@{self.revision}"


@dataclass(slots=True)
class AmbiguousTemplateRevisionError(Exception):
    action_id: str

    @override
    def __str__(self) -> str:
        return f"multiple activity template revisions supplied for: {self.action_id}"


@dataclass(slots=True)
class MissingPrerequisiteTemplateError(Exception):
    action_id: str
    prerequisite_action_id: str

    @override
    def __str__(self) -> str:
        return (
            "activity template prerequisite is absent from the injected catalog: "
            f"{self.action_id} -> {self.prerequisite_action_id}"
        )


@dataclass(slots=True)
class PrerequisiteCycleError(Exception):
    action_id: str

    @override
    def __str__(self) -> str:
        return f"activity template prerequisite cycle includes: {self.action_id}"


@dataclass(slots=True)
class UnknownOutcomeRequirementError(Exception):
    action_id: str
    requirement_key: str

    @override
    def __str__(self) -> str:
        return f"activity template outcome is absent from the editorial baseline: {self.requirement_key}"


@dataclass(frozen=True, slots=True)
class CandidateGenerator:
    """Expand all reviewed activities for unmet editorial requirements without selection."""

    templates: tuple[ActivityTemplate, ...]

    def __post_init__(self) -> None:
        catalog = self._catalog()
        self._validate_dependencies(catalog)

    def build(self, analysis: EditorialAnalysis) -> CandidateSet:
        """Return an identity-preserving candidate DAG and explicit planning blockers."""
        if analysis.analysis_status is AnalysisStatus.INSUFFICIENT_DATA:
            return self._candidate_set(
                analysis=analysis,
                candidates=(),
                blockers=(InsufficientAnalysisBlocker(analysis_id=analysis.analysis_id),),
            )
        return self._build_ready(analysis)

    def _build_ready(self, analysis: EditorialAnalysis) -> CandidateSet:
        catalog = self._catalog()
        evaluations = {item.requirement_key: item for item in analysis.requirements}
        self._validate_outcomes(catalog, frozenset(evaluations))
        unmet = tuple(
            item
            for item in sorted(analysis.requirements, key=lambda item: item.requirement_key)
            if item.status is SatisfactionStatus.UNMET
        )
        needs_input = tuple(
            NeedsInputBlocker(
                requirement_key=item.requirement_key,
                label=item.label,
                support_refs=item.support_refs,
            )
            for item in sorted(analysis.requirements, key=lambda item: item.requirement_key)
            if item.status is SatisfactionStatus.NEEDS_INPUT
        )
        roots = tuple(
            template
            for template in sorted(catalog.values(), key=lambda template: template.action_id)
            if template.outcome_requirement_keys
            and any(
                requirement.requirement_key in template.outcome_requirement_keys
                for requirement in unmet
            )
        )
        candidates = self._expand(roots, catalog, evaluations)
        roots_by_requirement = {
            requirement.requirement_key: tuple(
                template
                for template in roots
                if requirement.requirement_key in template.outcome_requirement_keys
            )
            for requirement in unmet
        }
        no_activities = tuple(
            NoActivitiesBlocker(
                requirement_key=requirement.requirement_key,
                label=requirement.label,
                support_refs=requirement.support_refs,
            )
            for requirement in unmet
            if not roots_by_requirement[requirement.requirement_key]
        )
        return self._candidate_set(
            analysis=analysis,
            candidates=candidates,
            blockers=needs_input + no_activities,
        )

    def _catalog(self) -> dict[str, ActivityTemplate]:
        catalog: dict[str, ActivityTemplate] = {}
        for template in self.templates:
            existing = catalog.get(template.action_id)
            if existing is not None:
                if existing.revision == template.revision:
                    raise DuplicateTemplateRevisionError(
                        action_id=template.action_id, revision=template.revision
                    )
                raise AmbiguousTemplateRevisionError(action_id=template.action_id)
            catalog[template.action_id] = template
        return catalog

    def _validate_dependencies(self, catalog: dict[str, ActivityTemplate]) -> None:
        for template in catalog.values():
            for prerequisite_action_id in template.prerequisite_action_ids:
                if prerequisite_action_id not in catalog:
                    raise MissingPrerequisiteTemplateError(
                        action_id=template.action_id,
                        prerequisite_action_id=prerequisite_action_id,
                    )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(action_id: str) -> None:
            if action_id in visiting:
                raise PrerequisiteCycleError(action_id=action_id)
            if action_id in visited:
                return
            visiting.add(action_id)
            for prerequisite_action_id in sorted(catalog[action_id].prerequisite_action_ids):
                visit(prerequisite_action_id)
            visiting.remove(action_id)
            visited.add(action_id)

        for action_id in sorted(catalog):
            visit(action_id)

    def _validate_outcomes(
        self, catalog: dict[str, ActivityTemplate], requirement_keys: frozenset[str]
    ) -> None:
        for template in catalog.values():
            missing = sorted(template.outcome_requirement_keys - requirement_keys)
            if missing:
                raise UnknownOutcomeRequirementError(
                    action_id=template.action_id, requirement_key=missing[0]
                )

    def _expand(
        self,
        roots: tuple[ActivityTemplate, ...],
        catalog: dict[str, ActivityTemplate],
        evaluations: dict[str, RequirementEvaluation],
    ) -> tuple[Candidate, ...]:
        candidates: list[Candidate] = []
        included_action_ids: set[str] = set()

        def is_satisfied_foundation(template: ActivityTemplate) -> bool:
            return (
                template.is_foundational
                and bool(template.outcome_requirement_keys)
                and all(
                    evaluations[requirement_key].status is SatisfactionStatus.SATISFIED
                    for requirement_key in template.outcome_requirement_keys
                )
            )

        def include(action_id: str) -> None:
            if action_id in included_action_ids:
                return
            template = catalog[action_id]
            fulfilled = tuple(
                sorted(
                    prerequisite_action_id
                    for prerequisite_action_id in template.prerequisite_action_ids
                    if is_satisfied_foundation(catalog[prerequisite_action_id])
                )
            )
            prerequisites = tuple(
                prerequisite_action_id
                for prerequisite_action_id in sorted(template.prerequisite_action_ids)
                if prerequisite_action_id not in fulfilled
            )
            for prerequisite_action_id in prerequisites:
                include(prerequisite_action_id)
            included_action_ids.add(action_id)
            candidates.append(
                Candidate(
                    action_id=template.action_id,
                    template_revision=template.revision,
                    title=template.title,
                    estimated_hours=template.estimated_hours,
                    outcome_requirement_keys=template.outcome_requirement_keys,
                    prerequisite_action_ids=prerequisites,
                    fulfilled_prerequisite_action_ids=fulfilled,
                    completion_criteria=template.completion_criteria,
                    support_refs=template.support_refs,
                    cost=template.cost,
                )
            )

        for root in roots:
            include(root.action_id)
        return tuple(candidates)

    def _candidate_set(
        self,
        analysis: EditorialAnalysis,
        candidates: tuple[Candidate, ...],
        blockers: tuple[InsufficientAnalysisBlocker | NeedsInputBlocker | NoActivitiesBlocker, ...],
    ) -> CandidateSet:
        return CandidateSet(
            analysis_id=analysis.analysis_id,
            profile_version=analysis.profile_version,
            occupation_id=analysis.occupation_id,
            basis_version=analysis.basis_version,
            corpus_release_id=analysis.corpus_release_id,
            is_fixture=analysis.is_fixture,
            reference_at=analysis.reference_at,
            candidates=candidates,
            blockers=blockers,
        )
