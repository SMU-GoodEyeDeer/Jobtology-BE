import re
from dataclasses import dataclass, field
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from jobtology_be.application.requirement_metadata import RequirementMetadataLookup
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult, Neo4jReadClient, Neo4jReadQuery
from jobtology_be.corpus.neo4j_queries import (
    ROLE_EVIDENCE_NCS_FIELDS,
    ROLE_EVIDENCE_NCS_QUERY_ID,
    ROLE_EVIDENCE_ONET_FIELDS,
    ROLE_EVIDENCE_ONET_QUERY_ID,
)
from jobtology_be.corpus.neo4j_repository import Neo4jRepositoryResponseError
from jobtology_be.product_roles.models import ProductRolePolicy, RolePolicy

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


class _Qualification(_Row):
    id: str
    code: str
    name: str
    units: int
    sessions: int
    latest_session: str | None


class _NcsOccupationRow(_Row):
    id: str
    code: str
    name: str
    postings: int
    sample_postings: list[_Posting]
    competencies: list[_Competency]
    onet: list[_OnetMatch]
    qualifications: list[_Qualification]


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
    ncs_base_code: str | None = None
    requirement_key: str | None = None
    qualification_code: str | None = None


class EvidenceEdge(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    source: str
    target: str
    kind: EvidenceEdgeKind
    weight: int | None = None


class RoleEvidenceGraph(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    occupation_id: str
    name: str
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]


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
) -> RoleEvidenceGraph:
    root = f"role:{role.occupation_id}"
    graph = _GraphBuilder({root: EvidenceNode(id=root, kind="role", label=role.name)})
    onet_by_code = {row.code: row for row in onet_rows}
    for ncs in ncs_rows:
        graph.link(root, EvidenceNode(
            id=ncs.id, kind="ncs_occupation", label=ncs.name,
            detail=f"NCS {ncs.code} · 연결 공고 {ncs.postings:,}건", weight=ncs.postings,
            ncs_category=ncs.code,
        ), EvidenceEdge(source=root, target=ncs.id, kind="MAPPED_TO"))
        if ncs.postings > 0:
            posting_id = f"postings:{ncs.code}"
            titles = [posting.title for posting in ncs.sample_postings if posting.title]
            graph.link(ncs.id, EvidenceNode(
                id=posting_id, kind="posting_evidence", label=f"공고 {ncs.postings:,}건",
                detail="\n".join(titles) or None, weight=ncs.postings,
                ncs_category=ncs.code,
            ), EvidenceEdge(source=ncs.id, target=posting_id, kind="EVIDENCED_BY", weight=ncs.postings))
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
            ), EvidenceEdge(source=ncs.id, target=match.id, kind="MATCHED_ONET", weight=match.shared))
            if (onet := onet_by_code.get(match.code)) is not None:
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
    except ValidationError as error:
        raise Neo4jRepositoryResponseError() from error


@dataclass(frozen=True, slots=True)
class Neo4jRoleEvidenceGraph:
    policy: ProductRolePolicy
    client: Neo4jReadClient = field(repr=False)
    requirement_metadata: RequirementMetadataLookup | None = field(default=None, repr=False)

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
            },
        ))
        ncs_rows = _records(ncs_result, ROLE_EVIDENCE_NCS_FIELDS, _NcsOccupationRow, len(codes))
        onet_codes = sorted({match.code for row in ncs_rows for match in row.onet})
        onet_rows: tuple[_OnetOccupationRow, ...] = ()
        if onet_codes:
            onet_result = await self.client.execute(Neo4jReadQuery(
                query_id=ROLE_EVIDENCE_ONET_QUERY_ID,
                parameters={
                    "codes": onet_codes, "limit": len(onet_codes),
                    "skill_limit": _SKILL_LIMIT, "technology_limit": _TECHNOLOGY_LIMIT,
                },
            ))
            onet_rows = _records(
                onet_result, ROLE_EVIDENCE_ONET_FIELDS, _OnetOccupationRow, len(onet_codes)
            )
        return build_role_evidence_graph(role, ncs_rows, onet_rows, self.requirement_metadata)
