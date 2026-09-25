from dataclasses import dataclass

from jobtology_be.contracts import NormalizedProfile
from jobtology_be.modules.analyses.editorial_models import (
    AnalysisStatus,
    BaselineMismatchError,
    Coverage,
    CoverageAvailability,
    EditorialAnalysis,
    EditorialAnalysisRequest,
    EditorialBaseline,
    EditorialRequirement,
    InputCompleteness,
    RequirementEvaluation,
    RequirementNecessity,
    SatisfactionStatus,
    UnavailableReason,
)


@dataclass(frozen=True, slots=True)
class EditorialGapAnalyzer:
    """Evaluate one injected reviewed editorial baseline without corpus access."""

    baseline: EditorialBaseline

    def evaluate(
        self,
        profile: NormalizedProfile,
        request: EditorialAnalysisRequest,
        completeness: InputCompleteness,
    ) -> EditorialAnalysis:
        if (
            request.occupation_id != self.baseline.occupation_id
            or request.basis_version != self.baseline.basis_version
        ):
            raise BaselineMismatchError(
                expected_occupation_id=self.baseline.occupation_id,
                expected_basis_version=self.baseline.basis_version,
                actual_occupation_id=request.occupation_id,
                actual_basis_version=request.basis_version,
            )
        evaluations = tuple(
            self._evaluate_requirement(requirement, profile, completeness)
            for requirement in self.baseline.requirements
        )
        baseline_empty = not self.baseline.requirements
        return EditorialAnalysis(
            analysis_id=request.analysis_id,
            profile_version=profile.profile_version,
            occupation_id=request.occupation_id,
            basis_version=request.basis_version,
            corpus_release_id=self.baseline.release.release_id,
            is_fixture=self.baseline.is_fixture,
            reference_at=request.reference_at,
            analysis_status=(
                AnalysisStatus.INSUFFICIENT_DATA if baseline_empty else AnalysisStatus.READY
            ),
            requirements=evaluations,
            required_coverage=self._coverage(
                evaluations, RequirementNecessity.REQUIRED, baseline_empty
            ),
            preferred_coverage=self._coverage(
                evaluations, RequirementNecessity.PREFERRED, baseline_empty
            ),
        )

    def _evaluate_requirement(
        self,
        requirement: EditorialRequirement,
        profile: NormalizedProfile,
        completeness: InputCompleteness,
    ) -> RequirementEvaluation:
        matches = tuple(
            capability
            for capability in profile.capabilities
            if capability.resolution == "RESOLVED"
            and capability.entity_id is not None
            and capability.entity_id == requirement.entity_id
        )
        if not matches:
            status = (
                SatisfactionStatus.UNMET
                if completeness.entities_complete
                else SatisfactionStatus.NEEDS_INPUT
            )
            return self._evaluation(requirement, status, frozenset())
        provenance = frozenset({"SELF_REPORTED"})
        observed_experience_codes = frozenset(
            code for capability in matches for code in capability.experience_codes
        )
        if requirement.required_experience_codes <= observed_experience_codes:
            return self._evaluation(requirement, SatisfactionStatus.SATISFIED, provenance)
        status = (
            SatisfactionStatus.UNMET
            if completeness.experience_is_complete_for(requirement.entity_id)
            else SatisfactionStatus.NEEDS_INPUT
        )
        return self._evaluation(requirement, status, provenance)

    def _evaluation(
        self,
        requirement: EditorialRequirement,
        status: SatisfactionStatus,
        provenance: frozenset[str],
    ) -> RequirementEvaluation:
        return RequirementEvaluation(
            requirement_key=requirement.requirement_key,
            label=requirement.label,
            necessity=requirement.necessity,
            status=status,
            provenance=provenance,
            support_refs=requirement.support_refs,
        )

    def _coverage(
        self,
        evaluations: tuple[RequirementEvaluation, ...],
        necessity: RequirementNecessity,
        baseline_empty: bool,
    ) -> Coverage:
        if baseline_empty:
            return Coverage(
                availability=CoverageAvailability.UNAVAILABLE,
                reason=UnavailableReason.EMPTY_BASELINE,
                score=None,
                matched_count=0,
                total_count=0,
                score_method=None,
            )
        matching = tuple(item for item in evaluations if item.necessity is necessity)
        if not matching:
            return Coverage(
                availability=CoverageAvailability.UNAVAILABLE,
                reason=UnavailableReason.NO_REQUIREMENTS,
                score=None,
                matched_count=0,
                total_count=0,
                score_method=None,
            )
        matched_count = sum(
            item.status is SatisfactionStatus.SATISFIED for item in matching
        )
        return Coverage(
            availability=CoverageAvailability.AVAILABLE,
            reason=None,
            score=matched_count / len(matching),
            matched_count=matched_count,
            total_count=len(matching),
            score_method="REVIEWED_CHECKLIST",
        )
