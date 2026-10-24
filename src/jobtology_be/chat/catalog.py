import asyncio
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlencode

from jobtology_be.api.live_source_models import CrosswalkResponse
from jobtology_be.application.m5_queries import M5DataUnavailableError
from jobtology_be.application.specs import SkillCatalog
from jobtology_be.chat.catalog_queries import EXPAND_ID, NODE_ID, PARENTS_ID, SEARCH_ID
from jobtology_be.chat.crosswalk import crosswalk_graph
from jobtology_be.chat.workspace_models import (
    EvidenceGraph,
    GraphEdge,
    GraphNode,
    edge,
    merge_graphs,
)
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError, Neo4jReadClient, Neo4jReadQuery
from jobtology_be.infrastructure.persistence.live_source_feed import (
    LiveSourceFeed,
    LiveSourceReadError,
)
from jobtology_be.modules.analyses.editorial_models import ReleaseState


class CatalogRetriever(Protocol):
    async def search(self, query: str, offset: int = 0) -> EvidenceGraph: ...
    async def expand(self, node_id: str, offset: int = 0) -> EvidenceGraph: ...
    async def posting(self, posting_id: str) -> EvidenceGraph: ...
    async def node(self, node_id: str) -> EvidenceGraph: ...
    async def search_postings(self, query: str, occupation_code: str | None = None, offset: int = 0) -> EvidenceGraph: ...


def posting_group(code: str) -> GraphNode:
    if not re.fullmatch(r"[0-9]{8}|[0-9]{2}-[0-9]{4}\.[0-9]{2}", code):
        raise ValueError("invalid occupation code")
    scheme = "onet:" if "-" in code else ""
    return GraphNode(id=f"postings:{scheme}{code}", kind="posting_evidence", label=f"채용공고 연결 · {code}",
                     occupation_code=code, basis="METADATA_INFERRED",
                     detail="메타데이터로 추론한 직무 연결이에요. 과거·미확인 연결은 별도로 표시하며 역량 요구로 인정하지 않아요.")


@dataclass
class SharedCatalogRetriever:
    client: Neo4jReadClient | None
    feed: LiveSourceFeed | None = None
    skills: SkillCatalog | None = None
    # Only public catalog reads are cached. This object never accepts account data.
    _cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def crosswalk_connections(self, response: CrosswalkResponse, node_id: str,
                                    context_ids: tuple[str, ...] = ()) -> EvidenceGraph:
        graph = crosswalk_graph(response, node_id, context_ids)
        if self.client is None or not graph.edges:
            return graph
        aliases = {}
        by_mapping = {edge.association_id: edge for edge in graph.edges}
        for item in response.items:
            if (item.mapping_id in by_mapping and item.source_freshness == "CURRENT"
                    and item.catalog_freshness == "CURRENT"):
                link = by_mapping[item.mapping_id]
                aliases[item.source.id], aliases[item.target.id] = link.source, link.target
        if not aliases:
            return graph
        key = ("crosswalk-parents", sha256(json.dumps([sorted(aliases.items()), sorted(context_ids)]).encode()).hexdigest(), 0)
        async with self._lock:
            cached = self._cache.get(key)
            parents = cached[1] if cached and cached[0] > time.monotonic() else None
        if parents is None:
            try:
                async with asyncio.timeout(2):
                    result = await self.client.execute(Neo4jReadQuery(query_id=PARENTS_ID,
                        parameters={"ids": list(aliases), "context_ids": list(context_ids), "limit": 24}))
                if result.fields != ("node", "source", "target", "kind") or len(result.rows) > 48:
                    raise ValueError("invalid crosswalk parent response")
                nodes, edges = {}, []
                for record, source, target, kind in result.rows:
                    if not all(isinstance(value, str) for value in (source, target, kind)):
                        raise ValueError("invalid crosswalk hierarchy edge")
                    node = GraphNode.model_validate(record)
                    nodes[node.id] = node
                    edges.append(edge(aliases.get(source, source), aliases.get(target, target), kind, "REFERENCE"))
                parents = EvidenceGraph(nodes=tuple(nodes.values()), edges=tuple(edges))
                async with self._lock:
                    self._cache[key] = (time.monotonic() + 30, parents)
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
            except (TimeoutError, Neo4jQueryApiError, ValueError):
                parents = EvidenceGraph(warnings=("CROSSWALK_UNAVAILABLE",))
        return merge_graphs(graph, parents)

    async def _read(self, operation: str, value: str, offset: int) -> EvidenceGraph:
        if self.client is None:
            return EvidenceGraph(warnings=("CATALOG_PARTIALLY_UNAVAILABLE",))
        key = (operation, sha256(value.encode()).hexdigest(), offset)
        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]
        if operation == "search":
            terms = list(dict.fromkeys(value.casefold().split()))[:6]
            if not terms:
                return EvidenceGraph()
            query = Neo4jReadQuery(query_id=SEARCH_ID,
                                   parameters={"terms": terms, "offset": offset, "limit": 25})
        else:
            query = Neo4jReadQuery(query_id=EXPAND_ID,
                                   parameters={"node_id": value, "offset": offset, "limit": 25})
        result = await self.client.execute(query)
        fields = ("node",) if operation == "search" else ("node", "source", "target", "kind")
        if result.fields != fields or len(result.rows) > 25:
            raise ValueError("invalid catalog response")
        nodes, edges = {}, {}
        for row in result.rows[:24]:
            record = dict(zip(fields, row, strict=True))
            node = GraphNode.model_validate(record["node"])
            nodes[node.id] = node
            if operation == "expand":
                item = edge(record["source"], record["target"], record["kind"], "REFERENCE")
                edges[item.id] = item
        graph = EvidenceGraph(nodes=tuple(nodes.values()), edges=tuple(edges.values()),
                              truncated=len(result.rows) == 25)
        async with self._lock:
            self._cache[key] = (time.monotonic() + 30, graph)
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
        return graph

    async def _neo4j_search(self, query: str, offset: int = 0) -> EvidenceGraph:
        graph = await self._read("search", query[:120].strip(), offset)
        # A search starts from the full catalog, then brings in a small number of
        # actual neighboring facts so the answer can explain relationships too.
        async def neighborhood(node):
            try:
                async with asyncio.timeout(2):
                    return await self.expand(node.id)
            except (TimeoutError, ValueError, LiveSourceReadError):
                return EvidenceGraph(warnings=("CATALOG_PARTIALLY_UNAVAILABLE",))
        async def safe_neighborhood(node):
            try:
                return await neighborhood(node)
            except Neo4jQueryApiError:
                return EvidenceGraph(warnings=("CATALOG_PARTIALLY_UNAVAILABLE",))
        if graph.nodes:
            neighbors = await asyncio.gather(*(safe_neighborhood(n) for n in graph.nodes[:2]))
            graph = merge_graphs(graph, *neighbors)
        return graph

    def _approved_search(self, query: str, offset: int) -> EvidenceGraph:
        if self.skills is None:
            return EvidenceGraph()
        matches = self.skills.search(query)
        skill_offset = offset // 24 * 12
        activity_offset = offset // 24 * 8
        selected = matches[skill_offset:skill_offset + 12]
        nodes = [GraphNode(id=item.entity_id, kind="ncs_competency" if item.source == "NCS" else "onet_skill",
                          label=item.label, detail=f"{item.original_label} · {', '.join(item.aliases[:8])}",
                          source_id=item.source, ncs_base_code=item.entity_id.rsplit(":", 1)[-1]
                          if item.source == "NCS" else None) for item in selected]
        links = []
        try:
            snapshots = self.skills.snapshots()
        except M5DataUnavailableError:
            snapshots = ()
        terms = query.casefold().split()[:6]
        activities = []
        for snapshot in snapshots:
            if snapshot.is_fixture or snapshot.release.state is not ReleaseState.PUBLISHED:
                continue
            requirements = {item.requirement_key: item for item in snapshot.requirements}
            aliases = {item.entity_id: ' '.join(item.aliases) for item in snapshot.capability_entries}
            for template in snapshot.templates:
                text = template.title + ' ' + ' '.join(
                    requirements[key].label + ' ' + aliases.get(requirements[key].entity_id, '')
                    for key in template.outcome_requirement_keys if key in requirements)
                if any(term in text.casefold() for term in terms):
                    activities.append((snapshot, requirements, template))
        for snapshot, requirements, template in activities[activity_offset:activity_offset + 8]:
            key = f"template:{snapshot.occupation_id}:{template.action_id}:{template.revision}"
            nodes.append(GraphNode(id=key, kind="activity", label=template.title,
                                   detail=f"승인된 학습 활동 · 예상 {template.estimated_hours}시간 · 비용은 아직 모델링하지 않아요.",
                                   source_pin=snapshot.release.release_id, basis="REFERENCE"))
            for outcome in template.outcome_requirement_keys:
                req = requirements.get(outcome)
                if req:
                    nodes.append(GraphNode(id=req.entity_id, kind="ncs_competency", label=req.label,
                                           source_pin=snapshot.release.release_id, basis="REVIEWED_REQUIREMENT"))
                    links.append(edge(key, req.entity_id, "TEACHES", "REVIEWED_REQUIREMENT"))
        return EvidenceGraph(nodes=tuple({n.id: n for n in nodes}.values()), edges=tuple(links),
                             truncated=len(matches) > skill_offset + 12 or len(activities) > activity_offset + 8)

    async def search(self, query: str, offset: int = 0) -> EvidenceGraph:
        query = query[:120].strip()
        async def postings():
            if self.feed is None:
                return EvidenceGraph()
            return await self.search_postings(query, offset=offset // 24 * 6)
        # Failure of one public source must not discard the others.
        async def safe(operation):
            try:
                async with asyncio.timeout(6):
                    return await operation
            except (TimeoutError, ValueError, LiveSourceReadError, Neo4jQueryApiError):
                return EvidenceGraph(warnings=("CATALOG_PARTIALLY_UNAVAILABLE",))
        native, jobs = await asyncio.gather(safe(self._neo4j_search(query, offset)), safe(postings()))
        # The approved catalog has its own page sizes, so map the shared page cursor.
        return merge_graphs(native, jobs, self._approved_search(query, offset))

    async def expand(self, node_id: str, offset: int = 0) -> EvidenceGraph:
        if node_id.startswith("postings:"):
            return await self.search_postings("", node_id.removeprefix("postings:").removeprefix("onet:"), offset // 24 * 6)
        if node_id.startswith("posting:"):
            return await self.posting(node_id)
        approved = self._approved_neighbors(node_id, offset)
        if approved is not None:
            found = approved
        else:
            anchor, neighbors = await asyncio.gather(self.node(node_id), self._read("expand", node_id, offset))
            found = merge_graphs(anchor, neighbors)
        if self.feed is None or not any(
            node.id == node_id and (node.kind in {"ncs_occupation", "ncs_competency", "onet_occupation", "onet_skill"}
                                    or node.kind == "technology" and node.id.startswith("urn:jobtology:onet-detail:"))
            for node in found.nodes
        ):
            return found
        # Optional catalog upgrades must not hide existing graph neighborhoods.
        reader = getattr(self.feed, "crosswalk", None)
        if reader is None:
            return found
        try:
            key = ("crosswalk", sha256(node_id.encode()).hexdigest(), offset)
            async with self._lock:
                cached = self._cache.get(key)
                mappings = cached[1] if cached and cached[0] > time.monotonic() else None
            if mappings is None:
                async with asyncio.timeout(6):
                    mappings = await self.crosswalk_connections(await reader(node_id, 24, offset), node_id)
                async with self._lock:
                    self._cache[key] = (time.monotonic() + 30, mappings)
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
            # Keep the anchor's existing approved/personal meaning and provenance.
            existing = {node.id for node in found.nodes}
            mappings = mappings.model_copy(update={"nodes": tuple(node for node in mappings.nodes if node.id not in existing)})
            return merge_graphs(found, mappings)
        except (TimeoutError, LiveSourceReadError, ValueError):
            return merge_graphs(found, EvidenceGraph(warnings=("CROSSWALK_UNAVAILABLE",)))

    def _approved_neighbors(self, node_id: str, offset: int) -> EvidenceGraph | None:
        # Application role IDs and normalized competency IDs are not necessarily
        # native Neo4j node IDs. Expand their approved artifact relationships directly.
        if self.skills is None or not node_id.startswith(("role:", "ncs:unit:")):
            return None
        try:
            snapshots = self.skills.snapshots()
        except M5DataUnavailableError:
            return None
        nodes, links, matches = [], [], []
        for snapshot in snapshots:
            if snapshot.is_fixture or snapshot.release.state is not ReleaseState.PUBLISHED:
                continue
            role_id = f"role:{snapshot.occupation_id}"
            if node_id == role_id:
                display = getattr(self.skills.metadata, "display_name", None)
                nodes.append(GraphNode(id=role_id, kind="role", label=(display(snapshot.occupation_id) if display else None) or snapshot.occupation_id,
                                       source_pin=snapshot.release.release_id))
                for req in snapshot.requirements:
                    matches.append((snapshot, role_id, req))
            elif any(req.entity_id == node_id for req in snapshot.requirements):
                reqs = {r.requirement_key: r for r in snapshot.requirements if r.entity_id == node_id}
                for template in snapshot.templates:
                    if reqs.keys() & template.outcome_requirement_keys:
                        matches.append((snapshot, template, next(iter(reqs.values()))))
        if not matches:
            return None
        for snapshot, source, req in matches[offset:offset + 24]:
            nodes.append(GraphNode(id=req.entity_id, kind="ncs_competency", label=req.label,
                                   requirement_key=req.requirement_key, source_pin=snapshot.release.release_id,
                                   basis="REVIEWED_REQUIREMENT"))
            if isinstance(source, str):
                links.append(edge(source, req.entity_id, "REQUIRES", "REVIEWED_REQUIREMENT"))
            else:
                key = f"template:{snapshot.occupation_id}:{source.action_id}:{source.revision}"
                nodes.append(GraphNode(id=key, kind="activity", label=source.title,
                    detail=f"예상 {source.estimated_hours}시간 · 비용은 아직 모델링하지 않아요.",
                    source_pin=snapshot.release.release_id))
                links.append(edge(key, req.entity_id, "TEACHES", "REVIEWED_REQUIREMENT"))
        return EvidenceGraph(nodes=tuple({n.id: n for n in nodes}.values()), edges=tuple(links),
                             truncated=len(matches) > offset + 24)

    async def node(self, node_id: str) -> EvidenceGraph:
        if node_id.startswith("postings:"):
            return EvidenceGraph(nodes=(posting_group(node_id.removeprefix("postings:").removeprefix("onet:")),))
        if node_id.startswith("posting:"):
            return await self.posting(node_id)
        if self.skills:
            item = next((item for item in self.skills.items() if item.entity_id == node_id), None)
            if item:
                return EvidenceGraph(nodes=(GraphNode(id=item.entity_id, label=item.label,
                    kind="ncs_competency" if item.source == "NCS" else "onet_skill",
                    detail=f"{item.original_label} · {', '.join(item.aliases[:8])}", source_id=item.source),))
            if node_id.startswith("template:"):
                try:
                    snapshots = self.skills.snapshots()
                except M5DataUnavailableError:
                    snapshots = ()
                for snapshot in snapshots:
                    if snapshot.is_fixture or snapshot.release.state is not ReleaseState.PUBLISHED:
                        continue
                    for template in snapshot.templates:
                        if node_id != f"template:{snapshot.occupation_id}:{template.action_id}:{template.revision}":
                            continue
                        node = GraphNode(id=node_id, kind="activity", label=template.title,
                            detail=f"승인된 학습 활동 · 예상 {template.estimated_hours}시간 · 비용은 아직 모델링하지 않아요.",
                            source_pin=snapshot.release.release_id, basis="REFERENCE")
                        nodes, links = [node], []
                        for req in snapshot.requirements:
                            if req.requirement_key in template.outcome_requirement_keys:
                                nodes.append(GraphNode(id=req.entity_id, kind="ncs_competency", label=req.label,
                                    basis="REVIEWED_REQUIREMENT", source_pin=snapshot.release.release_id))
                                links.append(edge(node_id, req.entity_id, "TEACHES", "REVIEWED_REQUIREMENT"))
                        return EvidenceGraph(nodes=tuple(nodes), edges=tuple(links))
        if self.client is None:
            return EvidenceGraph()
        result = await self.client.execute(Neo4jReadQuery(query_id=NODE_ID,
            parameters={"node_id": node_id, "limit": 1}))
        if result.fields != ("node",) or len(result.rows) > 1:
            raise ValueError("invalid catalog node response")
        return EvidenceGraph(nodes=tuple(GraphNode.model_validate(row[0]) for row in result.rows))

    def _posting_node(self, item) -> GraphNode:
        def bounded(value):
            if isinstance(value, str):
                return value[:300]
            if isinstance(value, list):
                return [str(v)[:80] for v in value[:8]]
            return value
        return GraphNode(id=f"posting:{item.posting_id}", kind="posting", label=(item.title or "채용공고")[:180],
                         detail=json.dumps({key: bounded(getattr(item, key, None)) for key in (
                             "source_status", "closing_date", "deadline_at", "deadline_kind", "ongoing", "source_id",
                             "organization_name", "education", "regions", "employment_types", "recruitment_type",
                             "headcount", "date_posted", "application_start_at", "application_start_date")}, ensure_ascii=False),
                         source_id=item.source_id,
                         source_pin=item.source_provenance.run_id if item.source_provenance else None,
                         href="/postings?" + urlencode({"posting": item.posting_id}), basis="REFERENCE")

    async def search_postings(self, query: str, occupation_code: str | None = None, offset: int = 0) -> EvidenceGraph:
        if self.feed is None:
            return EvidenceGraph(warnings=("POSTING_CONTEXT_UNAVAILABLE",))
        if occupation_code:
            group = posting_group(occupation_code)
            result = await self.feed.occupation_context([occupation_code], 6, offset)
            nodes = {group.id: group}
            links = []
            for association in result.items:
                if association.posting is None:
                    continue
                node = self._posting_node(association.posting)
                nodes[node.id] = node
                links.append(GraphEdge(id=f"association:{association.association_id}", source=group.id, target=node.id,
                    kind="INFERRED_POSTING_CONTEXT", basis="METADATA_INFERRED", association_id=association.association_id,
                    source_freshness=association.source_freshness, catalog_freshness=association.catalog_freshness,
                    review_status=association.review_status,
                    source_run_id=getattr(getattr(association, "source_pins", None), "run_id", None),
                    catalog_pin=getattr(getattr(association, "catalog_pins", None), "pin", None),
                    explanation="추론 직무 연결 · 원문 " + association.source_freshness + " · 분류 " + association.catalog_freshness))
            return EvidenceGraph(nodes=tuple(nodes.values()), edges=tuple(links), truncated=result.total > offset + len(result.items))
        query = query[:120].strip()
        if len(query) < 2:
            raise ValueError("posting search requires a query or occupation")
        result = await self.feed.postings(query, None, None, None, 6, offset)
        group = GraphNode(id="posting-search:" + sha256(query.encode()).hexdigest()[:16], kind="posting_evidence",
                          label=f"공고 검색 · {query}", detail="검색어에 일치한 공고예요. 직무 연결이나 역량 요구를 검증한 결과가 아니에요.",
                          href="/postings?" + urlencode({"q": query}),
                          basis="APPLICATION_CONTEXT_SELECTION")
        nodes = tuple(self._posting_node(item) for item in result.items)
        return EvidenceGraph(nodes=(group, *nodes) if nodes else (), edges=tuple(
            edge(group.id, node.id, "SEARCH_RESULT", "APPLICATION_CONTEXT_SELECTION") for node in nodes),
            truncated=result.total > offset + len(result.items))

    async def posting(self, posting_id: str) -> EvidenceGraph:
        if self.feed is None:
            return EvidenceGraph()
        result = await self.feed.posting(posting_id.removeprefix("posting:"))
        return EvidenceGraph(nodes=(self._posting_node(result.item),))
