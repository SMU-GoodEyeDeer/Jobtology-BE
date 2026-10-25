"""Posting classifications are exploration context, never competency requirements."""
import re

from jobtology_be.api.live_source_models import PostingItem
from jobtology_be.chat.workspace_models import EvidenceGraph, GraphEdge, GraphNode, edge


def posting_connections(item: PostingItem, posting: GraphNode, offset: int = 0) -> EvidenceGraph:
    nodes: dict[str, GraphNode] = {}
    links: dict[str, GraphEdge] = {}

    def occupation(scheme: str, code: str, name: str | None) -> GraphNode | None:
        pattern = r"[0-9]{8}" if scheme == "NCS" else r"[0-9]{2}-[0-9]{4}\.[0-9]{2}"
        if re.fullmatch(pattern, code) is None:
            return None
        identity = f"ncs:occupation:{code}" if scheme == "NCS" else f"urn:jobtology:onet:occupation:{code}"
        # Codes identify classification concepts. An inference's original version
        # and freshness belong to its edge, not to the current catalog node.
        return GraphNode(id=identity, kind="ncs_occupation" if scheme == "NCS" else "onet_occupation",
                         label=(name or f"{scheme} {code}")[:180], occupation_code=code,
                         detail=f"{scheme} 직무 분류 · {code}\n분류 연결만으로 이 공고의 필수 역량을 확정하지 않아요.")

    for category in sorted(getattr(item, "ncs_categories", ()), key=lambda value: (value.code or "", value.name or "")):
        # Broad source fields cannot identify a specific catalog occupation.
        node = occupation("NCS", category.code or "", category.name)
        if node is None:
            continue
        nodes.setdefault(node.id, node)
        link = edge(posting.id, node.id, "SOURCE_NCS_CLASSIFICATION", "REFERENCE",
                    "공고 원문에 명시된 NCS 직무 분류예요. 개별 역량 요구를 검증한 결과는 아니에요.")
        links[link.id] = link.model_copy(update={"source_run_id": posting.source_pin,
                                               "source_freshness": "CURRENT", "catalog_freshness": "UNKNOWN"})

    associations = sorted(getattr(item, "occupation_associations", ()), key=lambda value: value.association_id)
    for association in associations:
        node = occupation(association.occupation_scheme, association.occupation_code, association.occupation_name)
        if node is None:
            continue
        nodes.setdefault(node.id, node)
        link = GraphEdge(id=f"posting-classification:{association.association_id}", source=posting.id, target=node.id,
            kind="INFERRED_POSTING_OCCUPATION", basis="METADATA_INFERRED", association_id=association.association_id,
            source_freshness=association.source_freshness, catalog_freshness=association.catalog_freshness,
            review_status=association.review_status, source_run_id=association.source_pins.run_id,
            catalog_pin=association.catalog_pins.pin,
            explanation="메타데이터로 추론한 직무 분류예요. 카탈로그의 역량 목록은 이 공고의 검증된 필수 요건이 아니에요.")
        links[link.id] = link

    selected = list(links.values())[offset:offset + 24]
    targets = {link.target for link in selected}
    return EvidenceGraph(nodes=(posting, *(node for key, node in nodes.items() if key in targets)), edges=tuple(selected),
                         truncated=len(links) > offset + 24,
                         warnings=() if links else ("POSTING_CLASSIFICATIONS_MISSING",))
