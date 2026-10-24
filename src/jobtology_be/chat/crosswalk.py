"""Exploration-only catalog mappings; these never enter requirement evaluation."""
from jobtology_be.api.live_source_models import CrosswalkResponse
from jobtology_be.chat.workspace_models import Basis, EvidenceGraph, GraphEdge, GraphNode, NodeKind

_RELATIONS = {"RELATED": "관련", "PARTIAL": "일부 대응", "EXACT": "동등 대응"}
_KINDS: dict[str, NodeKind] = {kind: kind for kind in (
    "ncs_occupation", "ncs_competency", "onet_occupation", "onet_skill", "technology",
)}


def crosswalk_graph(response: CrosswalkResponse, anchor_id: str | None = None) -> EvidenceGraph:
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    for item in response.items:
        if item.status == "REJECTED":
            continue
        proposed = item.status == "PROPOSED"
        basis: Basis = "PROPOSED" if proposed else "REFERENCE"
        source_id, target_id = item.source.id, item.target.id
        # The application skill picker uses family/element aliases. Collapse only
        # a current resolved endpoint onto that alias; retain historical versions.
        if (anchor_id is not None and item.source.kind == "ncs_competency" and item.source_freshness == "CURRENT"
                and anchor_id == f"ncs:unit:{item.source.code[:10]}"):
            source_id = anchor_id
        if (anchor_id is not None and item.target.scheme == "ONET" and item.catalog_freshness == "CURRENT"
                and anchor_id == f"onet:skill:{item.target.code}"):
            target_id = anchor_id
        for endpoint, identity in ((item.source, source_id), (item.target, target_id)):
            kind = _KINDS.get(endpoint.kind, "onet_skill")
            nodes[identity] = GraphNode(
                id=identity, kind=kind, label=endpoint.name[:180], basis="REFERENCE",
                detail=f"{endpoint.scheme} {endpoint.code} · {endpoint.kind}\n{(endpoint.description or '')[:1000]}",
                source_pin=endpoint.catalog_pin,
                occupation_code=endpoint.code if endpoint.kind.endswith("_occupation") else None,
                ncs_base_code=endpoint.code[:10] if endpoint.kind == "ncs_competency" else None,
            )
        # The edge, rather than the reference node, carries the mapping's review state.
        review = item.review
        shared = item.evidence.get("shared_postings")
        detail = f"NCS↔O*NET {_RELATIONS[item.relation]} · {'검토 전 제안' if proposed else '검토된 대응'} · 역량 자동 인정 없음"
        if isinstance(shared, int) and not isinstance(shared, bool):
            detail += f" · 같은 공고 {shared}건"
        if review:
            detail += f" · {'사람' if review.reviewer_kind == 'HUMAN' else '보조'} 검토: {review.note[:300]}"
        edges.append(GraphEdge(
            id=f"crosswalk:{item.mapping_id}", source=source_id, target=target_id,
            kind=f"CROSSWALK_{item.relation}", basis=basis, explanation=detail,
            association_id=item.mapping_id, source_freshness=item.source_freshness,
            catalog_freshness=item.catalog_freshness,
            review_status="UNREVIEWED" if review is None else f"{review.reviewer_kind}_REVIEWED",
            source_run_id=item.source.catalog_pin, catalog_pin=item.target.catalog_pin,
        ))
    return EvidenceGraph(nodes=tuple(nodes.values()), edges=tuple(edges),
                         truncated=response.total > response.offset + len(response.items))
