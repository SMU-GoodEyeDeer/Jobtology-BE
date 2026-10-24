"""Keep a bounded, connected evidence packet around the user's question target."""
from collections.abc import Sequence

from jobtology_be.chat.workspace_models import EvidenceGraph


def focus_neighborhood(graph: EvidenceGraph, focus_ids: Sequence[str], limit: int = 24) -> EvidenceGraph:
    nodes = {node.id: node for node in graph.nodes}
    kept = dict.fromkeys(key for key in focus_ids if key in nodes)
    frontier = list(kept)
    root_is_posting = any(nodes[key].kind == "posting" for key in kept)
    for _ in range(3):
        neighbors = []
        for parent in frontier:
            candidates = []
            for link in graph.edges:
                key = link.target if link.source == parent else link.source if link.target == parent else None
                if key in nodes and key not in kept and not (root_is_posting and nodes[key].kind in {"posting", "posting_evidence"}):
                    candidates.append(key)
            neighbors.append(list(dict.fromkeys(candidates)))
        # Give each occupation room, rather than filling the packet with only
        # the first category's many skills.
        following = [row[i] for i in range(max(map(len, neighbors), default=0)) for row in neighbors if i < len(row)]
        frontier = []
        for key in dict.fromkeys(following):
            if key in nodes and len(kept) < limit:
                kept[key] = None
                frontier.append(key)
        if not frontier:
            break
    return EvidenceGraph(nodes=tuple(nodes[key] for key in kept),
                         edges=tuple(link for link in graph.edges if link.source in kept and link.target in kept),
                         warnings=graph.warnings, truncated=graph.truncated or len(kept) < len(nodes))
