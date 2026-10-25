"""Application requirement identities stay distinct from current catalog entries."""
import re

from jobtology_be.chat.workspace_models import EvidenceGraph, GraphNode, edge, merge_graphs


def catalog_lookup_id(node: GraphNode) -> str | None:
    if node.id.startswith("requirement:"):
        base = node.ncs_base_code
        return f"ncs:unit:{base}" if node.kind == "ncs_competency" and base and re.fullmatch(r"[0-9]{10}", base) else None
    return node.id


def with_catalog_reference(graph: EvidenceGraph, node: GraphNode, catalog_id: str) -> EvidenceGraph:
    if node.id == catalog_id or not any(item.id == catalog_id for item in graph.nodes):
        return graph
    return merge_graphs(graph, EvidenceGraph(nodes=(node,), edges=(edge(node.id, catalog_id, "CATALOG_REFERENCE", "REFERENCE",
        "현재 카탈로그 탐색용 연결 · 저장된 분석의 평가 근거는 유지돼요."),)))
