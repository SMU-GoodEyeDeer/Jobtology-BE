"""Structured self-reports and explicit, deterministic planning credit.

The weights are product planning assumptions, not a proficiency assessment. An entry
never gains competency credit merely from its title, category, or an LLM suggestion.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from importlib.resources import files
from typing import ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    StrictFloat,
    StrictInt,
    ValidationError,
    model_validator,
)

from jobtology_be.application.m5_queries import M5DataUnavailableError
from jobtology_be.application.queries import CapabilityView
from jobtology_be.application.requirement_metadata import RequirementMetadataLookup
from jobtology_be.contracts import CapabilityInput
from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot
from jobtology_be.modules.analyses.editorial_models import ReleaseState

type SpecCategory = Literal['skill', 'certification', 'test', 'project', 'experience', 'education', 'other']
type ResumeFieldName = Literal[
    'school', 'degree', 'major', 'organization', 'role', 'started_on', 'ended_on',
    'issuer', 'credential_code', 'issued_on', 'expires_on', 'score', 'score_max', 'score_unit', 'url',
]
type Proficiency = Literal['FAMILIAR', 'GUIDED', 'INDEPENDENT', 'PRODUCTION']
CREDIT = {'FAMILIAR': .25, 'GUIDED': .5, 'INDEPENDENT': .75, 'PRODUCTION': 1.0}
MIN_EVIDENCE_LENGTH = 20


class DocumentEvidence(BaseModel):
    """User-confirmed source quotation, not credential/proficiency verification."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    document_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser_revision: Literal['jobtology-resume-v1-7541e87']
    block_id: str = Field(pattern=r'^[a-f0-9]{24}$')
    text_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    quote: str = Field(min_length=1, max_length=2000)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    method: Literal['KEYWORD', 'AI_ASSISTED', 'SELECTED_TEXT']

    @model_validator(mode='after')
    def valid_span(self) -> Self:
        if self.end - self.start != len(self.quote):
            raise ValueError('source span differs from quotation')
        return self


class SpecDetails(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)

    competency_ids: tuple[str, ...] = Field(default=(), max_length=12)
    evidence: str = Field(default='', max_length=2000)
    source: Literal['MANUAL', 'LLM_ASSISTED', 'DOCUMENT_IMPORT'] = 'MANUAL'
    document_evidence: DocumentEvidence | None = None
    started_on: date | None = None
    ended_on: date | None = None
    ongoing: bool = False
    issuer: str | None = Field(default=None, max_length=200)
    credential_code: str | None = Field(default=None, max_length=128)
    issued_on: date | None = None
    expires_on: date | None = None
    score: StrictInt | StrictFloat | None = Field(default=None, ge=0, allow_inf_nan=False)
    score_max: StrictInt | StrictFloat | None = Field(default=None, gt=0, allow_inf_nan=False)
    score_unit: str | None = Field(default=None, max_length=64)
    organization: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    school: str | None = Field(default=None, max_length=200)
    degree: str | None = Field(default=None, max_length=200)
    major: str | None = Field(default=None, max_length=200)
    imported_fields: dict[ResumeFieldName, str] = Field(default_factory=dict, max_length=15)
    url: HttpUrl | None = None

    @model_validator(mode='after')
    def valid_details(self) -> Self:
        if (self.source == 'DOCUMENT_IMPORT') != (self.document_evidence is not None):
            raise ValueError('document imports require source evidence')
        if self.imported_fields and (self.document_evidence is None or any(
            not value or len(value) > 500 or value not in self.document_evidence.quote
            for value in self.imported_fields.values()
        )):
            raise ValueError('imported fields require exact source evidence')
        if len(set(self.competency_ids)) != len(self.competency_ids):
            raise ValueError('choose each competency once')
        if any(not value or len(value) > 128 for value in self.competency_ids):
            raise ValueError('invalid competency identity')
        if self.started_on and self.ended_on and self.ended_on < self.started_on:
            raise ValueError('end precedes start')
        if self.ongoing and self.ended_on:
            raise ValueError('ongoing experience cannot have an end')
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            raise ValueError('expiry precedes issue')
        if self.score_max is not None and self.score is not None and self.score > self.score_max:
            raise ValueError('score exceeds maximum')
        return self


class SkillItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    entity_id: str
    label: str
    source: Literal['NCS', 'ONET']
    original_label: str
    aliases: tuple[str, ...] = ()
    requirement_keys: tuple[str, ...] = ()
    ncs_level: int | None = None
    source_url: str
    translation_status: Literal['ORIGINAL_KO', 'EDITORIAL_KO']


class SpecMatching(BaseModel):
    status: Literal['MATCHED', 'NEEDS_CLARIFICATION', 'UNUSED', 'EXPIRED']
    credit: float
    competencies: tuple[SkillItem, ...]
    message: str
    policy: Literal['MODELED_COMPETENCY_CREDIT_V1'] = 'MODELED_COMPETENCY_CREDIT_V1'


def spec_credit(details: SpecDetails, proficiency: str | None, category: str, today: date) -> float:
    if category in {'education', 'other'}:
        return 0.0
    if category in {'certification', 'test'} and details.expires_on and details.expires_on < today:
        return 0.0
    if category in {'certification', 'test'} and details.issued_on and details.issued_on > today:
        return 0.0
    if not details.competency_ids or len(details.evidence.strip()) < MIN_EVIDENCE_LENGTH:
        return 0.0
    return CREDIT.get(proficiency or '', 0.0)


def capability_inputs(
    *, raw_text: str, entity_id: str | None, category: str, proficiency: str | None,
    details: Mapping[str, object], completed: bool = False, today: date | None = None,
) -> tuple[CapabilityInput, ...]:
    # Only the internal completion writer can set this provenance, never a spec POST.
    if completed and category == 'ROADMAP_OUTCOME':
        return (CapabilityInput(raw_text=raw_text, entity_id=entity_id,
                                experience_codes=list(details.get('experience_codes', ())), credit=1),)
    try:
        spec = SpecDetails.model_validate(dict(details))
    except ValidationError:
        return (CapabilityInput(raw_text=raw_text, credit=0),)
    credit = spec_credit(spec, proficiency, category, today or datetime.now(UTC).date())
    # An O*NET descriptor is not an NCS equivalence. Only confirmed NCS identities
    # enter the current NCS requirement evaluator; unmapped records stay visible.
    mapped = tuple(key for key in spec.competency_ids if key.startswith('ncs:unit:'))
    if not mapped:
        return (CapabilityInput(raw_text=raw_text, credit=0),)
    return tuple(CapabilityInput(raw_text=key.rsplit(':', 1)[-1], entity_id=key,
                                 credit=credit) for key in mapped)


class SkillCatalog:
    def __init__(self, snapshots: Callable[[], tuple[PublishedCorpusSnapshot, ...]],
                 metadata: RequirementMetadataLookup | None = None) -> None:
        self.snapshots = snapshots
        self.metadata = metadata
        self._onet = json.loads(files('jobtology_be.application').joinpath('onet_skills.json').read_text())

    def items(self, occupation_id: str | None = None) -> tuple[SkillItem, ...]:
        found: dict[str, SkillItem] = {}
        try:
            snapshots = self.snapshots()
        except M5DataUnavailableError:
            snapshots = ()
        for snapshot in snapshots:
            if (snapshot.is_fixture or snapshot.release.state is not ReleaseState.PUBLISHED
                    or not snapshot.release.release_id
                    or occupation_id and snapshot.occupation_id != occupation_id):
                continue
            aliases = {item.entity_id: item.aliases for item in snapshot.capability_entries}
            for requirement in snapshot.requirements:
                if not requirement.entity_id.startswith('ncs:unit:'):
                    continue
                previous = found.get(requirement.entity_id)
                meta = self.metadata.lookup(requirement.requirement_key) if self.metadata else None
                found[requirement.entity_id] = SkillItem(
                    entity_id=requirement.entity_id, label=requirement.label, source='NCS',
                    original_label=requirement.label,
                    aliases=tuple(sorted(set(aliases.get(requirement.entity_id, ()))
                                         | set(previous.aliases if previous else ()))),
                    requirement_keys=tuple(sorted({requirement.requirement_key,
                                                  *(previous.requirement_keys if previous else ())})),
                    ncs_level=meta.ncs_level if meta else None,
                    source_url='https://www.ncs.go.kr/', translation_status='ORIGINAL_KO',
                )
        for item in self._onet['items']:
            key = f"onet:skill:{item['code']}"
            found[key] = SkillItem(
                entity_id=key, label=item['label'], source='ONET', original_label=item['original_label'],
                aliases=(item['original_label'], item['label']),
                source_url=f"https://www.onetonline.org/find/descriptor/result/{item['code']}",
                translation_status='EDITORIAL_KO',
            )
        return tuple(found.values())

    def search(self, query: str, occupation_id: str | None = None) -> tuple[SkillItem, ...]:
        normalized = query.strip().casefold()
        matches = [item for item in self.items(occupation_id)
                   if all(token in ' '.join((item.label, item.original_label, item.entity_id,
                                            *item.aliases)).casefold()
                          for token in normalized.split())]
        return tuple(sorted(matches, key=lambda item: (
            not any(alias.casefold() == normalized for alias in (item.label, *item.aliases)),
            item.source != 'NCS', item.label, item.entity_id,
        )))

    def validate(self, details: SpecDetails) -> None:
        if not details.competency_ids:
            return
        supported = {item.entity_id for item in self.items()}
        if (any(key.startswith('ncs:unit:') for key in details.competency_ids)
                and not any(key.startswith('ncs:unit:') for key in supported)):
            raise M5DataUnavailableError(resource='skill catalog')
        if not set(details.competency_ids) <= supported:
            raise ValueError('unsupported competency identity')

    def matching(self, entry: CapabilityView, occupation_id: str | None,
                 today: date | None = None) -> SpecMatching:
        today = today or datetime.now(UTC).date()
        try:
            details = SpecDetails.model_validate(dict(entry.details))
        except ValidationError:
            # Internal roadmap completion records retain their own provenance.
            ids = (entry.entity_id,) if entry.source_completion_event_id else ()
            details = SpecDetails(competency_ids=ids)
        if entry.category in {'education', 'other'}:
            return SpecMatching(status='UNUSED', credit=0, competencies=(),
                                message='학력·기타 정보로 보관해요. 역량 점수에는 자동 반영하지 않아요.')
        global_items = {item.entity_id: item for item in self.items()}
        role_items = {item.entity_id: item for item in self.items(occupation_id)} if occupation_id else {}
        selected = tuple(global_items[key].model_copy(update={
            'requirement_keys': role_items[key].requirement_keys if key in role_items else (),
        }) for key in details.competency_ids if key in global_items)
        expired = (entry.category in {'certification', 'test'} and details.expires_on is not None
                   and details.expires_on < today)
        credit = spec_credit(details, entry.proficiency, entry.category, today)
        if entry.category == 'ROADMAP_OUTCOME' and entry.source_completion_event_id:
            credit = 1.0
        contributes = any(item.requirement_keys for item in selected)
        status = ('EXPIRED' if expired else 'NEEDS_CLARIFICATION' if not selected or not credit
                  else 'MATCHED' if contributes else 'UNUSED')
        message = {
            'EXPIRED': '유효기간이 지난 기록이에요. 새로 분석하면 계획에 반영하지 않아요.',
            'NEEDS_CLARIFICATION': '역량을 선택하고 20자 이상의 구체적인 경험을 적어 주세요. 저장만으로는 분석에 반영되지 않아요.',
            'MATCHED': '현재 목표 역량에 연결돼요. 본인 응답에 따른 계획 반영치이며 검증된 숙련도 점수가 아니에요.',
            'UNUSED': '기록은 보관하지만 현재 목표의 NCS 요구 역량과 연결되지 않아 분석에는 반영되지 않아요.',
        }[status]
        return SpecMatching(status=status, credit=credit if contributes else 0,
                            competencies=selected, message=message)


def details_document(details: SpecDetails) -> dict[str, JsonValue]:
    return details.model_dump(mode='json')
