from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel

from jobtology_be.application.queries import ProductJsonValue


@dataclass(frozen=True, slots=True)
class RequirementMetadata:
    ncs_level: int | None
    demand_pct: int | None
    estimated_hours: int | None
    hours_basis: Literal['OFFICIAL', 'ESTIMATED'] | None


class RequirementMetadataLookup(Protocol):
    def lookup(self, requirement_key: str) -> RequirementMetadata | None: ...


class OccupationDisplayNames(Protocol):
    def display_name(self, occupation_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    type: Literal['필수', '우대']
    demand_pct: int | None
    difficulty: str | None
    achievement: str | None


@dataclass(frozen=True, slots=True)
class AnalysisSummary:
    required_pct: int | None
    preferred_pct: int | None
    skills: tuple[SkillSummary, ...]


class _StoredCoverage(BaseModel):
    availability: Literal['AVAILABLE', 'UNAVAILABLE']
    score: float | None


class _StoredRequirement(BaseModel):
    requirement_key: str
    label: str
    necessity: Literal['REQUIRED', 'PREFERRED']
    status: Literal['SATISFIED', 'UNMET', 'NEEDS_INPUT']


class _StoredResults(BaseModel):
    required_coverage: _StoredCoverage | None = None
    preferred_coverage: _StoredCoverage | None = None
    requirements: tuple[_StoredRequirement, ...] = ()


def _coverage_pct(coverage: _StoredCoverage | None) -> int | None:
    if coverage is None or coverage.availability != 'AVAILABLE' or coverage.score is None:
        return None
    return round(coverage.score * 100)


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
        metadata = metadata_lookup.lookup(item.requirement_key) if metadata_lookup else None
        hours = metadata.estimated_hours if metadata else None
        skills.append(SkillSummary(
            name=item.label,
            type='필수' if item.necessity == 'REQUIRED' else '우대',
            demand_pct=metadata.demand_pct if metadata else None,
            difficulty=f'NCS 수준 {metadata.ncs_level}' if metadata and metadata.ncs_level is not None else None,
            achievement=(f'학습 {hours}시간' + (' (추정)' if metadata and metadata.hours_basis == 'ESTIMATED' else ''))
            if hours is not None else None,
        ))
    return AnalysisSummary(
        required_pct=_coverage_pct(stored.required_coverage),
        preferred_pct=_coverage_pct(stored.preferred_coverage),
        skills=tuple(skills),
    )
