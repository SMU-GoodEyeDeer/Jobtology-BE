"""Request-only evidence contracts. No private record is written to the catalog."""
from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


NodeKind = Literal[
    "role", "ncs_occupation", "ncs_competency", "onet_occupation", "onet_skill",
    "technology", "qualification", "exam_session", "posting", "posting_evidence",
    "publication", "profile", "goal", "capability", "analysis", "activity", "preferences",
]
Basis = Literal[
    "REFERENCE", "METADATA_INFERRED", "APPLICATION_CONTEXT_SELECTION",
    "USER_REPORTED", "APPLICATION_RECORD", "REVIEWED_REQUIREMENT", "PROPOSED",
]


class GraphNode(EvidenceModel):
    id: str
    kind: NodeKind
    label: str
    detail: str | None = None
    basis: Basis = "REFERENCE"
    href: str | None = None
    ncs_base_code: str | None = None
    requirement_key: str | None = None
    occupation_code: str | None = None
    qualification_code: str | None = None
    source_id: str | None = None
    source_pin: str | None = None


class GraphEdge(EvidenceModel):
    id: str
    source: str
    target: str
    kind: str
    basis: Basis
    explanation: str = ""


class EvidenceGraph(EvidenceModel):
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    truncated: bool = False
    warnings: tuple[str, ...] = ()


class EvidenceSource(EvidenceModel):
    id: str
    title: str
    text: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...] = ()
    basis: Basis
    href: str | None = None
    source_pin: str | None = None


class GraphAction(EvidenceModel):
    type: Literal["add", "highlight", "focus", "filter", "expand"]
    node_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    kinds: tuple[NodeKind, ...] = ()


class WorkspaceEvidence(EvidenceModel):
    goal_id: UUID | None = None
    occupation_id: str | None = None
    graph: EvidenceGraph = EvidenceGraph()
    sources: tuple[EvidenceSource, ...] = ()
    warnings: tuple[str, ...] = ()


class WorkspaceAnswer(WorkspaceEvidence):
    reply: str
    explanation: str
    actions: tuple[GraphAction, ...] = ()
    suggestions: tuple[str, ...] = ()


class CatalogRequest(EvidenceModel):
    q: str = Field(default="", max_length=120)
    node_id: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=10000)


def edge(source: str, target: str, kind: str, basis: Basis,
         explanation: str = "") -> GraphEdge:
    return GraphEdge(id=f"edge:{source}|{kind}|{target}", source=source, target=target,
                     kind=kind, basis=basis, explanation=explanation)


def merge_graphs(*graphs: EvidenceGraph, protected_node_ids: Iterable[str] = ()) -> EvidenceGraph:
    nodes = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes.pop(node.id, None)
            nodes[node.id] = node
    requested_ids = set(protected_node_ids)
    protected = [n for n in nodes.values() if n.id in requested_ids][:160]
    protected_ids = {n.id for n in protected}
    protected.extend([n for n in nodes.values() if n.id not in protected_ids
                      and n.kind in {"profile", "goal", "role", "preferences"}][:min(16, 160 - len(protected))])
    protected_ids = {n.id for n in protected}
    capacity = 160 - len(protected)
    recent = [n for n in nodes.values() if n.id not in protected_ids][-capacity:] if capacity else []
    kept = {n.id: n for n in (*protected, *recent)}
    edges = {item.id: item for graph in graphs for item in graph.edges
             if item.source in kept and item.target in kept}
    return EvidenceGraph(nodes=tuple(kept.values()), edges=tuple(edges.values())[:320],
                         warnings=tuple(dict.fromkeys(w for g in graphs for w in g.warnings)),
                         truncated=any(g.truncated for g in graphs) or len(nodes) > 160 or len(edges) > 320)


def sources_for(graph: EvidenceGraph) -> tuple[EvidenceSource, ...]:
    return tuple(EvidenceSource(
        id=node.id, title=node.label, text=(node.detail or node.label)[:1800],
        node_ids=(node.id,), edge_ids=tuple(e.id for e in graph.edges
                                          if node.id in (e.source, e.target)),
        basis=node.basis, href=node.href, source_pin=node.source_pin,
    ) for node in graph.nodes)
