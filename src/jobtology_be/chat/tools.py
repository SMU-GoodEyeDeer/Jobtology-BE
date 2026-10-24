"""Registered read-only tools. Account/goal scope is supplied by the server."""
import json
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jobtology_be.chat.catalog import CatalogRetriever
from jobtology_be.chat.personal import PersonalRetriever
from jobtology_be.chat.workspace_models import EvidenceGraph, sources_for


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Query(Arguments):
    query: str = Field(min_length=2, max_length=120)


class PostingQuery(Arguments):
    query: str = Field(max_length=120)
    occupation_code: str | None = Field(pattern=r"^(?:[0-9]{8}|[0-9]{2}-[0-9]{4}\.[0-9]{2})$")


class Node(Arguments):
    node_id: str = Field(min_length=1, max_length=500)


TOOL_MODELS = {"read_personal_records": Query, "search_catalog": Query,
               "search_postings": PostingQuery, "get_posting": Node, "expand_connections": Node}
TOOL_LABELS = {"read_personal_records": "내 정보 확인", "search_catalog": "카탈로그 검색",
               "search_postings": "채용공고 검색", "get_posting": "채용공고 상세 확인", "expand_connections": "그래프 연결 탐색"}
DESCRIPTIONS = {
    "read_personal_records": "Retrieve relevant profile, goals, specs/matching, analysis and roadmap records for the signed-in account and selected goal. No account IDs are accepted.",
    "search_catalog": "Search internal NCS/O*NET skills/occupations, qualifications, exams, publications and approved learning activities using a short Korean/English term.",
    "search_postings": "Search JobKorea/ALIO postings. Supply a short query and occupation_code=null for keyword search, or query='' and a known NCS/O*NET occupation code for inferred associations. Historical/stale associations stay labeled and are not verified requirements.",
    "get_posting": "Read available metadata of a posting node already retrieved. The source may not include full duties, benefits or salary; do not invent missing fields.",
    "expand_connections": "Expand a known graph node, including virtual posting groups, into real catalog or inferred posting connections. Never infer competency credit from a posting association.",
}
TOOLS = [{"type": "function", "name": name, "description": DESCRIPTIONS[name],
          "strict": True, "parameters": model.model_json_schema()} for name, model in TOOL_MODELS.items()]


@dataclass(frozen=True)
class ReadTools:
    personal: PersonalRetriever
    catalog: CatalogRetriever | None
    user_id: UUID | None
    goal_id: UUID | None

    async def execute(self, name: str, arguments: str, graph: EvidenceGraph) -> EvidenceGraph:
        if name not in TOOL_MODELS or len(arguments) > 4096:
            raise ValueError("unregistered tool or oversized arguments")
        args = TOOL_MODELS[name].model_validate_json(arguments)
        if name == "read_personal_records":
            if self.user_id is None:
                raise ValueError("account scope missing")
            context = await self.personal.retrieve(self.user_id, self.goal_id, args.query)
            return context.graph.model_copy(update={"warnings": tuple(dict.fromkeys((*context.graph.warnings, *context.warnings)))})
        if self.catalog is None:
            raise ValueError("catalog unavailable")
        if name == "search_catalog":
            return await self.catalog.search(args.query)
        if name == "search_postings":
            if args.occupation_code and not any(
                n.occupation_code == args.occupation_code or n.ncs_base_code and n.ncs_base_code[:8] == args.occupation_code
                for n in graph.nodes
            ):
                raise ValueError("occupation was not retrieved")
            return await self.catalog.search_postings(args.query, args.occupation_code)
        known = next((node for node in graph.nodes if node.id == args.node_id), None)
        if known is None:
            raise ValueError("unknown graph node")
        if name == "get_posting":
            if known.kind != "posting":
                raise ValueError("unknown posting")
            return await self.catalog.posting(args.node_id)
        return await self.catalog.expand(args.node_id)


def tool_output(graph: EvidenceGraph) -> str:
    nodes = {n.id: n for n in graph.nodes}
    return json.dumps({"sources": [{"id": s.id, "title": s.title[:180], "text": s.text[:300], "basis": s.basis,
                                    "occupation_code": nodes[s.id].occupation_code, "ncs_base_code": nodes[s.id].ncs_base_code}
                                   for s in sources_for(graph)[:10]],
                       "relationships": [e.model_dump(mode="json") for e in graph.edges[:12]],
                       "truncated": graph.truncated or len(graph.nodes) > 10 or len(graph.edges) > 12,
                       "warnings": graph.warnings}, ensure_ascii=False)
