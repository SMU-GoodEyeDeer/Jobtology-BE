from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from jobtology_be.application.queries import ProductJsonValue
from jobtology_be.planning.effort import REMAINING_EFFORT_METHOD, remaining_learning_hours

QualificationKind = Literal['NATIONAL_TECHNICAL', 'WORK_LEARNING']


@dataclass(frozen=True, slots=True)
class RequirementQualification:
    code: str
    name: str | None
    display_name: str
    kind: QualificationKind | None


@dataclass(frozen=True, slots=True)
class RequirementMetadata:
    ncs_level: int | None
    demand_pct: int | None
    estimated_hours: int | None
    hours_basis: Literal['OFFICIAL', 'ESTIMATED'] | None
    demand_postings: int | None = None
    demand_base: int | None = None
    ncs_base_code: str | None = None
    qualifications: tuple[RequirementQualification, ...] = ()
    field_code: str | None = None
    field_name: str | None = None


class RequirementMetadataLookup(Protocol):
    def lookup(self, requirement_key: str) -> RequirementMetadata | None: ...


class OccupationDisplayNames(Protocol):
    def display_name(self, occupation_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    type: Literal['필수', '우대']
    demand_pct: int | None
    demand_postings: int | None
    demand_base: int | None
    difficulty: str | None
    achievement: str | None
    requirement_key: str
    ncs_base_code: str | None
    status: Literal['PARTIAL', 'UNMET', 'NEEDS_INPUT']
    ncs_level: int | None
    estimated_hours: int | None
    hours_basis: Literal['OFFICIAL', 'ESTIMATED'] | None
    qualifications: tuple[RequirementQualification, ...]
    credit: float = 0.0
    base_estimated_hours: int | None = None


@dataclass(frozen=True, slots=True)
class AnalysisSummary:
    required_pct: int | None
    preferred_pct: int | None
    skills: tuple[SkillSummary, ...]
    weighted_required_pct: int | None = None
    weighted_preferred_pct: int | None = None
    weighted_score_method: str = REMAINING_EFFORT_METHOD


class _StoredCoverage(BaseModel):
    availability: Literal['AVAILABLE', 'UNAVAILABLE']
    score: float | None
    weighted_score: float | None = None


class _StoredRequirement(BaseModel):
    requirement_key: str
    label: str
    necessity: Literal['REQUIRED', 'PREFERRED']
    status: Literal['SATISFIED', 'PARTIAL', 'UNMET', 'NEEDS_INPUT']
    credit: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    base_estimated_hours: int | None = Field(default=None, ge=0)
    estimated_hours: int | None = Field(default=None, ge=0)
    metadata: RequirementMetadata | None = None


class _StoredResults(BaseModel):
    required_coverage: _StoredCoverage | None = None
    preferred_coverage: _StoredCoverage | None = None
    requirements: tuple[_StoredRequirement, ...] = ()


def _coverage_pct(coverage: _StoredCoverage | None, *, weighted: bool = False) -> int | None:
    if coverage is None or coverage.availability != 'AVAILABLE':
        return None
    score = coverage.weighted_score if weighted else coverage.score
    return round(score * 100) if score is not None else None


def build_analysis_summary(
    results: Mapping[str, ProductJsonValue] | None,
    metadata_lookup: RequirementMetadataLookup | None,
) -> AnalysisSummary | None:
    if results is None:
        return None
    stored = _StoredResults.model_validate(results)
    skills: list[SkillSummary] = []
    for item in sorted(stored.requirements, key=lambda item: (item.necessity != 'REQUIRED', item.label)):
        if item.status == 'SATISFIED':
            continue
        metadata = (item.metadata if "metadata" in item.model_fields_set else
                    metadata_lookup.lookup(item.requirement_key) if metadata_lookup else None)
        base_hours = item.base_estimated_hours if item.base_estimated_hours is not None else metadata.estimated_hours if metadata else None
        hours = item.estimated_hours if item.estimated_hours is not None else (
            remaining_learning_hours(base_hours, item.credit) if base_hours is not None else None
        )
        partial = item.status == 'PARTIAL'
        skills.append(SkillSummary(
            name=item.label,
            type='필수' if item.necessity == 'REQUIRED' else '우대',
            demand_pct=metadata.demand_pct if metadata else None,
            demand_postings=metadata.demand_postings if metadata else None,
            demand_base=metadata.demand_base if metadata else None,
            difficulty=f'NCS 수준 {metadata.ncs_level}' if metadata and metadata.ncs_level is not None else None,
            achievement=(f'추가 학습 {hours}시간 (부분 인정 적용 · 추정)' if partial else
                         f'학습 {hours}시간' + (' (추정)' if metadata and metadata.hours_basis == 'ESTIMATED' else ''))
            if hours is not None else None,
            requirement_key=item.requirement_key,
            ncs_base_code=metadata.ncs_base_code if metadata else None,
            status=item.status,
            ncs_level=metadata.ncs_level if metadata else None,
            estimated_hours=hours,
            hours_basis='ESTIMATED' if partial else metadata.hours_basis if metadata else None,
            qualifications=metadata.qualifications if metadata else (),
            credit=item.credit,
            base_estimated_hours=base_hours,
        ))
    return AnalysisSummary(
        required_pct=_coverage_pct(stored.required_coverage),
        preferred_pct=_coverage_pct(stored.preferred_coverage),
        skills=tuple(skills),
        weighted_required_pct=_coverage_pct(stored.required_coverage, weighted=True),
        weighted_preferred_pct=_coverage_pct(stored.preferred_coverage, weighted=True),
    )
