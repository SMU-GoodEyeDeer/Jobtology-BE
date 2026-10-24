import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from test_acceptance_m5_worker import CAPABILITY_LABEL, _snapshot

from jobtology_be.api.chat import require_chat_rate_limiter
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import (
    AnalysisView,
    CapabilityView,
    GoalView,
    ProfileView,
    RoadmapStepView,
    RoadmapView,
)
from jobtology_be.application.services.preferences import RoutePreferences
from jobtology_be.application.specs import SkillCatalog
from jobtology_be.chat.catalog import SharedCatalogRetriever
from jobtology_be.chat.catalog_queries import SEARCH_ID, WORKSPACE_QUERIES
from jobtology_be.chat.personal import PersonalRetriever
from jobtology_be.chat.workspace import ChatWorkspaceService
from jobtology_be.chat.workspace_models import (
    EvidenceGraph,
    GraphNode,
    WorkspaceEvidence,
    edge,
    merge_graphs,
    sources_for,
)
from jobtology_be.corpus.neo4j_client import Neo4jQueryResult
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.rate_limit import RateLimitDecision
from jobtology_be.llm.client import LlmMessage, LlmUnavailableError
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER, GOAL, OTHER, ANALYSIS = uuid4(), uuid4(), uuid4(), uuid4()
NOW = datetime.now(UTC)


def personal_fixture():
    goal = GoalView(GOAL, "TARGETED", "BACKEND_DEVELOPER", NOW, "Asia/Seoul", "", "ACTIVE", NOW, NOW)
    profile = ProfileView(USER, 4, "컴퓨터공학", None, 3, "ENROLLED", None)
    identity = "ncs:unit:2001020211"
    spec = CapabilityView(uuid4(), "project", "Python API 개발", None, "GUIDED", "SELF_REPORTED", "ACTIVE",
        {"competency_ids": [identity], "evidence": "Python으로 REST API를 구현하고 테스트를 작성한 프로젝트 경험입니다."}, None, NOW, NOW)
    original = _snapshot()
    snapshot = replace(original,
                       requirements=tuple(replace(item, entity_id=identity) for item in original.requirements),
                       capability_entries=tuple(replace(item, entity_id=identity) for item in original.capability_entries))
    skills = SkillCatalog(lambda: (snapshot,))
    queries = SimpleNamespace(
        get_goal=AsyncMock(return_value=goal), get_profile=AsyncMock(return_value=profile),
        list_goals=AsyncMock(return_value=(goal, replace(goal, goal_id=OTHER, occupation_id="DATA_ANALYST"))),
        list_capabilities=AsyncMock(return_value=(spec,)), list_roadmaps=AsyncMock(return_value=()),
        get_analysis=AsyncMock(return_value=AnalysisView(ANALYSIS, GOAL, 3, "EDITORIAL", "v1", None, "m1", "READY", NOW, {
            "requirements": [{"requirement_key": "BACKEND_DEVELOPER:2001020211", "label": CAPABILITY_LABEL,
                              "necessity": "REQUIRED", "status": "PARTIAL", "credit": .5}],
        })), get_roadmap=AsyncMock(),
    )
    m5 = SimpleNamespace(get_dashboard=AsyncMock(return_value=SimpleNamespace(analysis=SimpleNamespace(analysis_id=ANALYSIS))))
    prefs = SimpleNamespace(get_preferences=AsyncMock(return_value=RoutePreferences(4, 10, "USER", "REGULAR", None, False, True, False)))
    return PersonalRetriever(queries, m5, prefs, skills), queries, m5


def test_personal_retrieval_preserves_real_matching_credit_and_analysis_version():
    personal, queries, m5 = personal_fixture()
    result = anyio.run(personal.retrieve, USER, GOAL, "Python")
    spec = next(n for n in result.graph.nodes if n.kind == "capability")
    assert json.loads(spec.detail)["matching"]["credit"] == .5
    assert "ANALYSIS_PROFILE_OUTDATED" in result.warnings
    assert "NO_ROADMAP" in result.warnings
    assert next(n for n in result.graph.nodes if n.kind == "analysis").id == f"analysis:{ANALYSIS}"
    assert all(call.args[0] == USER for method in vars(queries).values() for call in method.await_args_list)
    m5.get_dashboard.assert_awaited_once_with(USER, GOAL)


def test_foreign_goal_stops_before_any_other_account_read():
    personal, queries, _ = personal_fixture()
    queries.get_goal.side_effect = MissingRecordError(resource="goal")
    with pytest.raises(MissingRecordError):
        anyio.run(personal.retrieve, USER, uuid4(), "")
    queries.get_profile.assert_not_awaited()
    queries.list_capabilities.assert_not_awaited()


def test_no_goal_does_not_pick_an_arbitrary_active_goal_or_analysis():
    personal, _, m5 = personal_fixture()
    result = anyio.run(personal.retrieve, USER, None, "")
    assert result.goal_id is None and result.occupation_id is None
    m5.get_dashboard.assert_not_awaited()
    assert not any(n.kind in {"analysis", "activity"} for n in result.graph.nodes)


def test_unavailable_catalog_does_not_present_unknown_matching_as_zero_credit():
    from jobtology_be.application.m5_queries import M5DataUnavailableError
    personal, _, _ = personal_fixture()
    def missing():
        raise M5DataUnavailableError(resource="catalog")
    personal = replace(personal, skills=SkillCatalog(missing))
    result = anyio.run(personal.retrieve, USER, GOAL, "")
    spec = next(n for n in result.graph.nodes if n.kind == "capability")
    matching = json.loads(spec.detail)["matching"]
    assert matching["status"] == "UNAVAILABLE" and matching["credit"] is None
    assert "SKILL_CATALOG_UNAVAILABLE" in result.warnings


def test_schedule_prerequisites_and_completed_state_are_retrieved_from_selected_roadmap():
    personal, queries, _ = personal_fixture()
    a, b, rid = uuid4(), uuid4(), uuid4()
    steps = (RoadmapStepView(a, "a", 0, "a", 1, "COMPLETED", NOW, NOW, (), (), (), "기초"),
             RoadmapStepView(b, "b", 1, "b", 1, "TODO", NOW, NOW, (), (), (a,), "응용"))
    roadmap = RoadmapView(rid, 2, "ACTIVE", GOAL, uuid4(), "경로", 3, None, {}, steps)
    queries.list_roadmaps.return_value = (replace(roadmap, roadmap_id=uuid4(), goal_id=OTHER), roadmap)
    queries.get_roadmap.return_value = roadmap
    result = anyio.run(personal.retrieve, USER, GOAL, "")
    queries.get_roadmap.assert_awaited_once_with(USER, rid)
    assert any(e.kind == "PREREQUISITE" and e.source == f"step:{a}" and e.target == f"step:{b}" for e in result.graph.edges)
    assert json.loads(next(n.detail for n in result.graph.nodes if n.id == f"step:{a}"))["state"] == "COMPLETED"


class Model:
    def __init__(self, plan=None, answer=None):
        self.calls = []
        self.planned = False
        self.plan = plan or {"operations": []}
        self.answer = answer or {"reply": "저장된 근거를 확인했어요.", "explanation": "내 스펙과 연결된 자료예요.",
            "source_ids": ["personal", "invented"], "node_ids": ["personal", "invented"],
            "edge_ids": ["fake"], "filter_kinds": [], "suggestions": [], "proposed_connections": []}

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.plan if kwargs["schema_name"] == "chat_retrieval_plan" else self.answer

    async def stream_response(self, **kwargs):
        schema = kwargs.get("schema_name")
        if schema:
            answer = await self.complete_json(**kwargs)
            text = json.dumps(answer, ensure_ascii=False)
            for i in range(0, len(text), 3):
                yield {"type": "response.output_text.delta", "delta": text[i:i + 3]}
            yield {"type": "response.completed", "response": {"output": []}}
            return
        plan = await self.complete_json(**{**kwargs, "schema_name": "chat_retrieval_plan"})
        output = []
        if not self.planned:
            self.planned = True
            for i, operation in enumerate(plan["operations"]):
                key = "query" if operation["operation"] == "search_catalog" else "node_id"
                output.append({"type": "function_call", "call_id": f"call-{i}", "name": operation["operation"],
                               "arguments": json.dumps({key: operation["value"]})})
        yield {"type": "response.completed", "response": {"output": output}}


def initial():
    graph = EvidenceGraph(nodes=(GraphNode(id="personal", kind="capability", label="내 스펙", basis="USER_REPORTED"),))
    return WorkspaceEvidence(goal_id=GOAL, graph=graph, sources=sources_for(graph))


async def collect(service, context=None, focus=()):
    return [event async for event in service.events(context or initial(), (LlmMessage("user", "내 기록을 설명해줘"),), focus)]


def test_answer_rejects_invented_sources_nodes_and_edges_and_bounds_model_rounds():
    model = Model()
    service = ChatWorkspaceService(None, model)
    events = anyio.run(collect, service)
    answer = next(e["data"] for e in events if e["type"] == "answer")
    assert [s["id"] for s in answer["sources"]] == ["personal"]
    assert answer["actions"][1]["node_ids"] == ["personal"]
    assert answer["actions"][1]["edge_ids"] == []
    assert len(model.calls) == 2
    assert events[-1] == {"type": "done"}


def test_broader_search_adds_new_catalog_knowledge_and_only_expands_known_ids():
    model = Model({"operations": [{"operation": "search_catalog", "value": "SQL"},
                                  {"operation": "expand_connections", "value": "unknown"}]})
    catalog = SimpleNamespace(search=AsyncMock(return_value=EvidenceGraph(nodes=(GraphNode(id="new", kind="technology", label="SQL"),))), expand=AsyncMock())
    events = anyio.run(collect, ChatWorkspaceService(None, model, catalog))
    assert any(any(n["id"] == "new" for n in e["data"]["graph"]["nodes"]) for e in events if e["type"] == "evidence")
    catalog.expand.assert_not_awaited()


def test_planner_failure_preserves_personal_evidence_and_answer():
    model = Model()
    original = model.complete_json
    async def failure(**kwargs):
        if kwargs["schema_name"] == "chat_retrieval_plan":
            raise LlmUnavailableError("offline")
        return await original(**kwargs)
    model.complete_json = failure
    events = anyio.run(collect, ChatWorkspaceService(None, model))
    answer = next(e["data"] for e in events if e["type"] == "answer")
    assert "SEARCH_PLANNER_UNAVAILABLE" in answer["warnings"]
    assert answer["graph"]["nodes"][0]["id"] == "personal"


def test_proposed_connections_are_labeled_and_cannot_invent_endpoints():
    model = Model()
    model.answer["proposed_connections"] = [
        {"source": "personal", "target": "sql", "explanation": "학습 연결을 제안해요."},
        {"source": "personal", "target": "invented", "explanation": "invalid"},
    ]
    graph = merge_graphs(initial().graph, EvidenceGraph(nodes=(GraphNode(id="sql", kind="technology", label="SQL"),)))
    result = anyio.run(collect, ChatWorkspaceService(None, model), WorkspaceEvidence(graph=graph))
    answer = next(e["data"] for e in result if e["type"] == "answer")
    assert len(answer["graph"]["edges"]) == 1
    assert answer["graph"]["edges"][0]["basis"] == "PROPOSED"
    assert answer["graph"]["edges"][0]["kind"] == "SUGGESTED_LINK"


def test_catalog_timeout_keeps_personal_results_and_surfaces_a_partial_warning():
    model = Model({"operations": [{"operation": "search_catalog", "value": "SQL"}]})
    catalog = SimpleNamespace(search=AsyncMock(side_effect=TimeoutError))
    result = anyio.run(collect, ChatWorkspaceService(None, model, catalog))
    answer = next(e["data"] for e in result if e["type"] == "answer")
    assert "CATALOG_PARTIALLY_UNAVAILABLE" in answer["warnings"]
    assert any(n["id"] == "personal" for n in answer["graph"]["nodes"])


def test_catalog_uses_bound_parameters_and_a_separate_bounded_public_cache():
    async def run():
        client = SimpleNamespace(execute=AsyncMock(return_value=Neo4jQueryResult(fields=("node",), rows=(), query_type=None)))
        catalog = SharedCatalogRetriever(client)
        await catalog.search("SQL ' MATCH (secret)")
        await catalog.search("SQL ' MATCH (secret)")
        assert client.execute.await_count == 1
        query = client.execute.call_args.args[0]
        assert query.query_id == SEARCH_ID and query.parameters["limit"] == 25
        assert "'" in query.parameters["terms"]
        assert all("SQL" not in q.statement and "RETURN *" not in q.statement for q in WORKSPACE_QUERIES)
    anyio.run(run)


def test_approved_learning_activities_and_korean_english_aliases_work_without_neo4j():
    async def run():
        skills = SkillCatalog(lambda: (_snapshot(),))
        catalog = SharedCatalogRetriever(None, skills=skills)
        result = await catalog.search("API")
        activity = next(n for n in result.nodes if n.kind == "activity")
        assert any(e.kind == "TEACHES" for e in result.edges)
        assert activity.source_pin == _snapshot().release.release_id
        focused = await catalog.node(activity.id)
        assert any(n.id == activity.id for n in focused.nodes)
        role = await catalog.expand("role:BACKEND_DEVELOPER")
        assert any(e.kind == "REQUIRES" for e in role.edges)
        onet = next(item for item in skills.items() if item.source == "ONET")
        ko = await catalog.search(onet.label)
        en = await catalog.search(onet.original_label)
        assert any(n.id == onet.entity_id for n in ko.nodes)
        assert any(n.id == onet.entity_id for n in en.nodes)
    anyio.run(run)


def test_compacted_personal_records_remain_valid_bounded_json():
    from jobtology_be.chat.personal import compact
    text = compact({"matching": {"credit": .5, "status": "MATCHED"}, "evidence": "\x00" * 9000})
    assert len(text) <= 1800
    assert json.loads(text)["matching"]["credit"] == .5


def test_saved_weekly_overrides_are_read_without_repacking_the_schedule():
    from datetime import date

    from jobtology_be.application.weekly_plan_models import PlanOverrides, WeeklyPlanStep
    personal, queries, _ = personal_fixture()
    step_id, rid = uuid4(), uuid4()
    step = RoadmapStepView(step_id, "a", 0, "a", 1, "TODO", NOW, NOW, (), (), (), "API 활동")
    roadmap = RoadmapView(rid, 2, "ACTIVE", GOAL, uuid4(), "경로", 3, None, {}, (step,))
    queries.list_roadmaps.return_value = (roadmap,)
    queries.get_roadmap.return_value = roadmap
    saved = WeeklyPlanStep(step_id=step_id, number=1, recommended_number=1, title="API 활동", name="API", requirement_key=None,
        state="TODO", phase="BASIC", ncs_level=None, field_code=None, field_name=None, hours=20, recommended_hours=20,
        hours_basis="CUSTOM", demand_pct=None, start_week=3, end_week=4, start_date=date(2027, 1, 18), end_date=date(2027, 1, 31),
        reason="사용자 일정", moved=False, prerequisite_step_ids=(), unmet_prerequisite_step_ids=())
    overrides = PlanOverrides(weekly_hours=10, rest_weeks=(2,), order=(str(step_id),), hours={}, saved_at=NOW, version=3,
                              step_schedules={str(step_id): saved})
    weekly = SimpleNamespace(get=AsyncMock(return_value=overrides))
    result = anyio.run(replace(personal, weekly=weekly).retrieve, USER, GOAL, "")
    detail = json.loads(next(n.detail for n in result.graph.nodes if n.id == f"step:{step_id}"))
    assert detail["current_start"] == "2027-01-18" and detail["current_end"] == "2027-01-31"
    assert detail["schedule_source"] == "CUSTOM" and detail["weekly_plan_version"] == 3
    weekly.get.assert_awaited_once_with(USER, rid)


def test_graph_caps_and_removes_dangling_edges():
    graph = EvidenceGraph(nodes=tuple(GraphNode(id=str(i), kind="technology", label=str(i)) for i in range(200)),
                          edges=(edge("0", "199", "REFERENCE", "REFERENCE"),))
    bounded = merge_graphs(graph)
    assert len(bounded.nodes) == 160 and bounded.truncated and not bounded.edges


def test_graph_cap_preserves_explicit_personal_nodes_and_their_relationships():
    personal = EvidenceGraph(nodes=(
        GraphNode(id="capability", kind="capability", label="경험"),
        GraphNode(id="analysis", kind="analysis", label="분석"),
        GraphNode(id="activity", kind="activity", label="활동"),
    ), edges=(edge("capability", "analysis", "MATCHED_TO", "USER_REPORTED"),
              edge("analysis", "activity", "PLANNED_ACTIVITY", "APPLICATION_RECORD")))
    catalog = EvidenceGraph(nodes=tuple(GraphNode(id=str(i), kind="technology", label=str(i)) for i in range(200)))
    bounded = merge_graphs(personal, catalog, protected_node_ids={n.id for n in personal.nodes})
    assert len(bounded.nodes) == 160 and bounded.truncated
    assert {n.id for n in personal.nodes} <= {n.id for n in bounded.nodes}
    assert bounded.edges == personal.edges
    full = merge_graphs(catalog, EvidenceGraph(nodes=(GraphNode(id="extra", kind="role", label="extra"),)),
                        protected_node_ids={str(i) for i in range(160)})
    assert len(full.nodes) == 160
    assert {n.id for n in full.nodes} == {str(i) for i in range(160)}


def test_edge_cap_preserves_personal_and_latest_retrieved_relationships():
    nodes = tuple(GraphNode(id=key, kind="technology", label=key) for key in ("personal", "a", "b"))
    personal = edge("personal", "a", "MATCHED_TO", "USER_REPORTED")
    old = tuple(edge("a", "b", f"REFERENCE_{i}", "REFERENCE") for i in range(320))
    refreshed = old[0].model_copy(update={"explanation": "updated"})
    newest = edge("a", "b", "INFERRED_POSTING_CONTEXT", "METADATA_INFERRED")
    result = merge_graphs(EvidenceGraph(nodes=nodes, edges=(personal, *old)),
                          EvidenceGraph(edges=(refreshed, newest)), protected_node_ids={"personal"})
    assert result.truncated and len(result.edges) == 320
    assert personal in result.edges and result.edges[-2:] == (refreshed, newest)
    assert old[1] not in result.edges


def test_stream_protects_initial_evidence_through_focus_search_and_proposed_link_merges():
    context = initial()
    context = context.model_copy(update={"graph": merge_graphs(context.graph, EvidenceGraph(
        nodes=(GraphNode(id="analysis", kind="analysis", label="내 분석"),),
        edges=(edge("personal", "analysis", "MATCHED_TO", "USER_REPORTED"),),
    ))})
    def catalog_page(prefix):
        return EvidenceGraph(nodes=tuple(GraphNode(id=f"{prefix}:{i}", kind="technology", label="SQL") for i in range(160)))
    model = Model({"operations": [{"operation": "search_catalog", "value": "SQL"}]})
    model.answer["proposed_connections"] = [{"source": "personal", "target": "analysis", "explanation": "학습 연결 제안"}]
    catalog = SimpleNamespace(node=AsyncMock(return_value=catalog_page("focus")),
                              search=AsyncMock(return_value=catalog_page("search")))
    events = anyio.run(collect, ChatWorkspaceService(None, model, catalog), context, ("focus:0",))
    graphs = [e["data"]["graph"] for e in events if e["type"] in {"evidence", "answer"}]
    assert events[-1] == {"type": "done"}
    for graph in graphs:
        assert len(graph["nodes"]) == 160
        assert {"personal", "analysis"} <= {n["id"] for n in graph["nodes"]}
        assert any(e["kind"] == "MATCHED_TO" for e in graph["edges"])
    assert any(e["kind"] == "SUGGESTED_LINK" for e in graphs[-1]["edges"])
    assert any(n["id"].startswith("search:") for n in graphs[-1]["nodes"])


def test_cancelling_model_work_releases_service_capacity():
    async def run():
        model = Model()
        waiting = asyncio.Event()
        async def blocked(**kwargs):
            waiting.set()
            await asyncio.Event().wait()
        model.complete_json = blocked
        service = ChatWorkspaceService(None, model)
        task = asyncio.create_task(collect(service))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._slots._value == 4
    anyio.run(run)


def test_stream_checks_ownership_before_headers_and_preserves_original_chat_modes():
    personal, queries, _ = personal_fixture()
    service = ChatWorkspaceService(personal, Model())
    class Principal:
        async def current_principal(self):
            return AuthenticatedPrincipal(user_id=USER)
    principal = Principal()
    limiter = SimpleNamespace(consume=AsyncMock(return_value=RateLimitDecision(True, 0)))
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(identity_provider=principal, chat_workspace=service))
    app.dependency_overrides[require_chat_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        queries.get_goal.side_effect = MissingRecordError(resource="goal")
        response = client.post("/api/v1/chat/stream", json={"goal_id": str(uuid4()), "messages": [{"role": "user", "text": "hello"}]})
        assert response.status_code == 404
        queries.get_goal.side_effect = None
        response = client.post("/api/v1/chat/stream", json={"goal_id": str(GOAL), "messages": [{"role": "user", "text": "hello"}]})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert json.loads(response.text.splitlines()[-1]) == {"type": "done"}


def test_context_quota_rejects_before_personal_reads():
    personal, queries, _ = personal_fixture()
    class Principal:
        async def current_principal(self):
            return AuthenticatedPrincipal(user_id=USER)
    limiter = SimpleNamespace(consume=AsyncMock(return_value=RateLimitDecision(False, 10)))
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(
        identity_provider=Principal(), chat_workspace=ChatWorkspaceService(personal)))
    app.dependency_overrides[require_chat_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        response = client.get("/api/v1/chat/context", params={"goal_id": str(GOAL)})
    assert response.status_code == 429 and response.headers["retry-after"] == "10"
    queries.get_goal.assert_not_awaited()
