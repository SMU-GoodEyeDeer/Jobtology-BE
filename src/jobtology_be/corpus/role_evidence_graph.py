import re
from dataclasses import dataclass, field
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict

from jobtology_be.application.requirement_metadata import RequirementMetadataLookup
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult, Neo4jReadClient, Neo4jReadQuery
from jobtology_be.corpus.neo4j_queries import (
    ROLE_CONTEXT_ONET_FIELDS,
    ROLE_CONTEXT_ONET_QUERY_ID,
    ROLE_EVIDENCE_NCS_FIELDS,
    ROLE_EVIDENCE_NCS_QUERY_ID,
    ROLE_EVIDENCE_ONET_FIELDS,
    ROLE_EVIDENCE_ONET_QUERY_ID,
)
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryResponseError
from jobtology_be.infrastructure.persistence.live_source_feed import (
    LiveSourceFeed,
    LiveSourceReadError,
)
from jobtology_be.product_roles.models import ProductRolePolicy, RolePolicy
from jobtology_be.product_roles.posting_context import posting_context_policy

EvidenceNodeKind = Literal[
    "role",
    "ncs_occupation",
    "posting_evidence",
    "ncs_competency",
    "onet_occupation",
    "onet_skill",
    "qualification",
    "technology",
]
EvidenceEdgeKind = Literal[
    "MAPPED_TO", "EVIDENCED_BY", "HAS_COMPETENCY", "MATCHED_ONET", "ESSENTIAL_SKILL", "HOT_TECHNOLOGY",
    "QUALIFIED_BY",
    "CONTEXT_FOR",
]

_POSTING_SAMPLE_LIMIT: Final = 3
_COMPETENCY_LIMIT: Final = 3
_ONET_PER_NCS_LIMIT: Final = 1
_SKILL_LIMIT: Final = 2
_TECHNOLOGY_LIMIT: Final = 4
_QUALIFICATION_LIMIT: Final = 2


class _Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)


class _Posting(_Row):
    id: str
    title: str | None
    source_id: str | None


class _Competency(_Row):
    id: str
    code: str
    name: str
    links: int


class _OnetMatch(_Row):
    id: str
    code: str
    name: str
    shared: int
    review_statuses: list[str] = []
    stages: list[str] = []
    source_run_ids: list[str] = []
    catalog_pins: list[str] = []


class _Qualification(_Row):
    id: str
    code: str
    name: str
    units: int
    sessions: int
    latest_session: str | None


class EvidenceSource(_Row):
    source_id: str | None
    source_run_id: str | None
    catalog_run_id: str | None
    source_current: bool
    catalog_current: bool
    review_status: str
    stage: str
    accepted: bool
    postings: int
    basis: Literal["METADATA_INFERRED"] = "METADATA_INFERRED"
    confidence: None = None
    confidence_state: Literal["UNASSESSED"] = "UNASSESSED"


class _NcsOccupationRow(_Row):
    id: str
    code: str
    name: str
    postings: int
    sample_postings: list[_Posting]
    competencies: list[_Competency]
    onet: list[_OnetMatch]
    qualifications: list[_Qualification]
    evidence_sources: list[EvidenceSource] = []
    excluded_associations: int = 0


class _OnetContextRow(_Row):
    id: str
    code: str
    name: str
    postings: int | None = None


class _Skill(_Row):
    id: str
    name: str
    importance: float


class _Technology(_Row):
    id: str
    name: str
    in_demand: bool


class _OnetOccupationRow(_Row):
    id: str
    code: str
    name: str
    skills: list[_Skill]
    technologies: list[_Technology]


class EvidenceNode(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str
    kind: EvidenceNodeKind
    label: str
    detail: str | None = None
    weight: int | None = None
    ncs_category: str | None = None
    occupation_code: str | None = None
    ncs_base_code: str | None = None
    requirement_key: str | None = None
    qualification_code: str | None = None
    evidence_sources: tuple[EvidenceSource, ...] = ()
    excluded_associations: int = 0
    basis: Literal["METADATA_INFERRED", "REFERENCE", "APPLICATION_CONTEXT_SELECTION"] = "REFERENCE"
    review_statuses: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()


class EvidenceEdge(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    source: str
    target: str
    kind: EvidenceEdgeKind
    weight: int | None = None
    basis: Literal["METADATA_INFERRED", "REFERENCE", "APPLICATION_CONTEXT_SELECTION"] = "REFERENCE"
    evidence_sources: tuple[EvidenceSource, ...] = ()
    review_statuses: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    catalog_pins: tuple[str, ...] = ()


class RoleEvidenceGraph(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    occupation_id: str
    name: str
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    warnings: tuple[Literal['POSTING_CONTEXT_UNAVAILABLE'], ...] = ()


@dataclass(slots=True)
class _GraphBuilder:
    nodes: dict[str, EvidenceNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], EvidenceEdge] = field(default_factory=dict)

    def link(self, source: str, node: EvidenceNode, edge: EvidenceEdge) -> None:
        _ = self.nodes.setdefault(node.id, node)
        _ = self.edges.setdefault((source, node.id, edge.kind), edge)


def build_role_evidence_graph(
    role: RolePolicy,
    ncs_rows: tuple[_NcsOccupationRow, ...],
    onet_rows: tuple[_OnetOccupationRow, ...],
    requirement_metadata: RequirementMetadataLookup | None = None,
    *, context_onet_rows: tuple[_OnetContextRow, ...] = (),
) -> RoleEvidenceGraph:
    root = f"role:{role.occupation_id}"
    graph = _GraphBuilder({root: EvidenceNode(id=root, kind="role", label=role.name)})
    onet_by_id = {row.id: row for row in onet_rows}
    for ncs in ncs_rows:
        graph.link(root, EvidenceNode(
            id=ncs.id, kind="ncs_occupation", label=ncs.name,
            detail=f"NCS {ncs.code} · 현재 원문·분류 기준의 추정 연결 {ncs.postings:,}건"
                   + (f"\n기준이 바뀐 과거 연결 {ncs.excluded_associations:,}건은 집계에서 제외했어요."
                      if ncs.excluded_associations else ""), weight=ncs.postings,
            ncs_category=ncs.code,
            evidence_sources=tuple(ncs.evidence_sources), excluded_associations=ncs.excluded_associations,
        ), EvidenceEdge(source=root, target=ncs.id, kind="MAPPED_TO"))
        if ncs.postings > 0 or ncs.excluded_associations > 0:
            posting_id = f"postings:{ncs.code}"
            titles = [posting.title for posting in ncs.sample_postings if posting.title]
            graph.link(ncs.id, EvidenceNode(
                id=posting_id, kind="posting_evidence", label=f"추정 공고 {ncs.postings:,}건" if ncs.postings else f"과거 공고 연결 {ncs.excluded_associations:,}건",
                detail="\n".join(titles) if ncs.postings else "기준이 바뀐 과거 연결이에요. 현재 연결이나 역량 요구로 반영하지 않아요.", weight=ncs.postings,
                ncs_category=ncs.code,
                excluded_associations=ncs.excluded_associations,
                evidence_sources=tuple(ncs.evidence_sources), basis="METADATA_INFERRED",
            ), EvidenceEdge(source=ncs.id, target=posting_id, kind="EVIDENCED_BY", weight=ncs.postings,
                            basis="METADATA_INFERRED", evidence_sources=tuple(
                                source for source in ncs.evidence_sources
                                if source.source_current and source.catalog_current)))
        for unit in ncs.competencies:
            reviewed = f" · 검토된 공고 연결 {unit.links}건" if unit.links else ""
            match = re.fullmatch(r"([0-9]{10})(?:_[A-Za-z0-9.]+)?", unit.code)
            base_code = match[1] if match else None
            candidate_key = f"{role.occupation_id}:{base_code}" if base_code else None
            metadata = requirement_metadata.lookup(candidate_key) if (
                requirement_metadata is not None and candidate_key is not None
            ) else None
            graph.link(ncs.id, EvidenceNode(
                id=unit.id, kind="ncs_competency", label=unit.name,
                detail=f"NCS 능력단위 {unit.code}{reviewed}", weight=unit.links,
                ncs_base_code=base_code,
                requirement_key=candidate_key if metadata is not None
                and metadata.ncs_base_code == base_code else None,
            ), EvidenceEdge(source=ncs.id, target=unit.id, kind="HAS_COMPETENCY"))
        for qualification in ncs.qualifications:
            graph.link(ncs.id, _qualification_node(qualification), EvidenceEdge(
                source=ncs.id, target=qualification.id, kind="QUALIFIED_BY", weight=qualification.units,
            ))
        for match in ncs.onet:
            graph.link(ncs.id, EvidenceNode(
                id=match.id, kind="onet_occupation", label=match.name,
                detail=f"O*NET {match.code} · 같은 공고 {match.shared:,}건에서 함께 매칭",
                weight=match.shared,
                occupation_code=match.code,
                basis="METADATA_INFERRED",
                review_statuses=tuple(match.review_statuses), stages=tuple(match.stages),
            ), EvidenceEdge(source=ncs.id, target=match.id, kind="MATCHED_ONET", weight=match.shared,
                            basis="METADATA_INFERRED", review_statuses=tuple(match.review_statuses),
                            stages=tuple(match.stages), source_run_ids=tuple(match.source_run_ids),
                            catalog_pins=tuple(match.catalog_pins)))
            if (onet := onet_by_id.get(match.id)) is not None:
                _link_onet_details(graph, onet)
    for context in context_onet_rows:
        graph.link(root, EvidenceNode(
            id=context.id, kind="onet_occupation", label=context.name,
            detail=f"O*NET {context.code} · 목표 직무의 탐색용 참고 직업\n"
                   "NCS와의 동등성이나 역량 충족을 뜻하지 않아요.",
            occupation_code=context.code, basis="APPLICATION_CONTEXT_SELECTION",
        ), EvidenceEdge(source=root, target=context.id, kind="CONTEXT_FOR",
                        basis="APPLICATION_CONTEXT_SELECTION"))
        if context.postings:
            posting_id = f"postings:onet:{context.code}"
            graph.link(context.id, EvidenceNode(
                id=posting_id, kind="posting_evidence", label=f"추정 연결 {context.postings:,}건",
                detail=f"O*NET {context.code} · 최신 원문·분류 기준의 추정 연결 수\n"
                       "연결 수는 중복을 제외한 공고 수와 다를 수 있어요.",
                weight=context.postings, occupation_code=context.code,
                basis="METADATA_INFERRED",
            ), EvidenceEdge(source=context.id, target=posting_id, kind="EVIDENCED_BY",
                            weight=context.postings, basis="METADATA_INFERRED"))
        if (onet := onet_by_id.get(context.id)) is not None:
            _link_onet_details(graph, onet)
    return RoleEvidenceGraph(
        occupation_id=role.occupation_id,
        name=role.name,
        nodes=tuple(graph.nodes.values()),
        edges=tuple(graph.edges.values()),
    )


def _qualification_node(qualification: _Qualification) -> EvidenceNode:
    schedule = (
        f"\n시험 일정 {qualification.sessions}건 · 최근 {qualification.latest_session}"
        if qualification.sessions and qualification.latest_session else ""
    )
    return EvidenceNode(
        id=qualification.id, kind="qualification", label=qualification.name,
        detail=f"Q-Net 자격 {qualification.code} · 이 직무 능력단위 {qualification.units}개 포함{schedule}",
        weight=qualification.units,
        qualification_code=qualification.code,
    )


def _link_onet_details(graph: _GraphBuilder, onet: _OnetOccupationRow) -> None:
    for skill in onet.skills:
        graph.link(onet.id, EvidenceNode(
            id=skill.id, kind="onet_skill", label=skill.name,
            detail=f"O*NET 핵심 역량 · 중요도 {skill.importance:.2f}/5",
        ), EvidenceEdge(source=onet.id, target=skill.id, kind="ESSENTIAL_SKILL"))
    for technology in onet.technologies:
        demand = " · 채용 수요 높음" if technology.in_demand else ""
        graph.link(onet.id, EvidenceNode(
            id=technology.id, kind="technology", label=technology.name,
            detail=f"O*NET 핫 테크놀로지{demand}",
        ), EvidenceEdge(source=onet.id, target=technology.id, kind="HOT_TECHNOLOGY"))


def _records[RowT: _Row](
    result: Neo4jQueryResult, fields: tuple[str, ...], model: type[RowT], limit: int
) -> tuple[RowT, ...]:
    if result.fields != fields or len(result.rows) > limit:
        raise Neo4jRepositoryResponseError()
    try:
        return tuple(model.model_validate(dict(zip(fields, row, strict=True))) for row in result.rows)
    except ValueError as error:
        raise Neo4jRepositoryResponseError() from error


@dataclass(frozen=True, slots=True)
class Neo4jRoleEvidenceGraph:
    policy: ProductRolePolicy
    client: Neo4jReadClient = field(repr=False)
    requirement_metadata: RequirementMetadataLookup | None = field(default=None, repr=False)
    context_feed: LiveSourceFeed | None = field(default=None, repr=False)

    async def graph(self, occupation_id: str) -> RoleEvidenceGraph | None:
        role = next((r for r in self.policy.roles if r.occupation_id == occupation_id), None)
        if role is None:
            return None
        codes = sorted({*role.occupation_codes, *(base[:8] for base in role.additional_base_codes)})
        ncs_result = await self.client.execute(Neo4jReadQuery(
            query_id=ROLE_EVIDENCE_NCS_QUERY_ID,
            parameters={
                "codes": list(codes), "limit": len(codes),
                "posting_limit": _POSTING_SAMPLE_LIMIT,
                "competency_limit": _COMPETENCY_LIMIT, "onet_limit": _ONET_PER_NCS_LIMIT,
                "qualification_limit": _QUALIFICATION_LIMIT,
                "provenance_limit": 24,
            },
        ))
        ncs_rows = _records(ncs_result, ROLE_EVIDENCE_NCS_FIELDS, _NcsOccupationRow, len(codes))
        context_codes = posting_context_policy().roles.get(occupation_id, ())
        context_rows = ()
        warnings = ()
        if context_codes:
            context_result = await self.client.execute(Neo4jReadQuery(
                query_id=ROLE_CONTEXT_ONET_QUERY_ID,
                parameters={"codes": list(context_codes), "limit": len(context_codes)},
            ))
            context_rows = _records(context_result, ROLE_CONTEXT_ONET_FIELDS, _OnetContextRow, len(context_codes))
            if context_rows:
                if self.context_feed is None:
                    warnings = ('POSTING_CONTEXT_UNAVAILABLE',)
                else:
                    try:
                        context = await self.context_feed.occupation_context(list(context_codes), 1, 0)
                        counts = {row.occupation_code: row.current_associations
                                  for row in context.summary.by_occupation if row.occupation_scheme == 'ONET'}
                        context_rows = tuple(row.model_copy(update={'postings': counts.get(row.code, 0)})
                                             for row in context_rows)
                    except LiveSourceReadError:
                        warnings = ('POSTING_CONTEXT_UNAVAILABLE',)
        onet_ids = sorted({match.id for row in ncs_rows for match in row.onet} | {row.id for row in context_rows})
        onet_rows: tuple[_OnetOccupationRow, ...] = ()
        if onet_ids:
            onet_result = await self.client.execute(Neo4jReadQuery(
                query_id=ROLE_EVIDENCE_ONET_QUERY_ID,
                parameters={
                    "ids": onet_ids, "limit": len(onet_ids),
                    "skill_limit": _SKILL_LIMIT, "technology_limit": _TECHNOLOGY_LIMIT,
                },
            ))
            onet_rows = _records(
                onet_result, ROLE_EVIDENCE_ONET_FIELDS, _OnetOccupationRow, len(onet_ids)
            )
        graph = build_role_evidence_graph(role, ncs_rows, onet_rows, self.requirement_metadata,
                                         context_onet_rows=context_rows)
        return graph.model_copy(update={'warnings': warnings})
