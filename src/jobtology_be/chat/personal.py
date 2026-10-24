"""Read-only, account-scoped retrieval using the same assessment policy as the UI."""
import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from jobtology_be.application.m5_queries import M5DataUnavailableError, M5Queries
from jobtology_be.application.queries import ProductQueries
from jobtology_be.application.requirement_metadata import (
    RequirementMetadataLookup,
    build_analysis_summary,
)
from jobtology_be.application.services.preferences import PreferencesService
from jobtology_be.application.specs import SkillCatalog
from jobtology_be.application.weekly_plan_store import WeeklyPlanStore
from jobtology_be.chat.workspace_models import (
    EvidenceGraph,
    GraphNode,
    WorkspaceEvidence,
    edge,
    sources_for,
)
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError


def compact(value: object) -> str:
    def trim(item: object, budget: int, depth: int = 0):
        if depth > 4:
            return None
        if isinstance(item, Mapping):
            found, remaining = {}, budget - 2
            for key, child in list(item.items())[:24]:
                key = str(key)[:80]
                key_cost = len(json.dumps(key, ensure_ascii=False)) + 2
                if remaining - key_cost < 8:
                    break
                kept = trim(child, min(700, remaining - key_cost), depth + 1)
                cost = key_cost + len(json.dumps(kept, ensure_ascii=False))
                if cost <= remaining:
                    found[key] = kept
                    remaining -= cost
            return found
        if isinstance(item, (list, tuple)):
            found, remaining = [], budget - 2
            for child in item[:16]:
                if remaining < 8:
                    break
                kept = trim(child, min(500, remaining - 1), depth + 1)
                cost = len(json.dumps(kept, ensure_ascii=False)) + 1
                if cost <= remaining:
                    found.append(kept)
                    remaining -= cost
            return found
        if item is None or isinstance(item, (bool, int, float)):
            return item
        # JSON escaping of hostile control characters uses at most six code points.
        return str(item)[:min(700, max(0, (budget - 2) // 6))]
    return json.dumps(trim(value, 1800), ensure_ascii=False)


def ranked(items, query: str, text, limit: int):
    tokens = re.findall(r"[\w+#.]{2,}", query.casefold())[:16]
    return sorted(items, key=lambda item: -sum(token in text(item).casefold() for token in tokens))[:limit]


@dataclass(frozen=True)
class PersonalRetriever:
    queries: ProductQueries
    m5: M5Queries
    preferences: PreferencesService
    skills: SkillCatalog
    metadata: RequirementMetadataLookup | None = None
    weekly: WeeklyPlanStore | None = None

    async def retrieve(self, user_id: UUID, goal_id: UUID | None, query: str) -> WorkspaceEvidence:
        # Explicit ownership check precedes any selected-goal reads. No implicit
        # first-goal fallback: null deliberately means a general account question.
        goal = await self.queries.get_goal(user_id, goal_id) if goal_id else None
        profile, goals, capabilities, preferences = await asyncio.gather(
            self.queries.get_profile(user_id), self.queries.list_goals(user_id),
            self.queries.list_capabilities(user_id), self.preferences.get_preferences(user_id),
        )
        root = f"goal:{goal_id}" if goal else "account:profile"
        def occupation_name(value):
            display_name = getattr(self.metadata, "display_name", None)
            return (display_name(value) if display_name and value else value) or "목표 미정"
        nodes = [GraphNode(id="account:profile", kind="profile", label="내 프로필",
                           detail=compact(asdict(profile) | {"user_id": None}),
                           basis="USER_REPORTED", href="/myinfo")]
        links = []
        for item in ranked(goals, query, lambda item: item.occupation_id or "", 6):
            node = GraphNode(id=f"goal:{item.goal_id}", kind="goal",
                             label="나의 목표" if item.goal_id == goal_id else f"{occupation_name(item.occupation_id)} 목표", detail=compact(asdict(item)),
                             basis="APPLICATION_RECORD", href=f"/myinfo?goal_id={item.goal_id}")
            nodes.append(node)
            links.append(edge("account:profile", node.id, "HAS_GOAL", "APPLICATION_RECORD"))
        if goal and not any(node.id == root for node in nodes):
            nodes.append(GraphNode(id=root, kind="goal", label="나의 목표",
                                   detail=compact(asdict(goal)), basis="APPLICATION_RECORD"))
        if goal and goal.occupation_id:
            role_id = f"role:{goal.occupation_id}"
            nodes.append(GraphNode(id=role_id, kind="role", label=occupation_name(goal.occupation_id)))
            links.append(edge(root, role_id, "TARGETS", "APPLICATION_RECORD"))
        nodes.append(GraphNode(id="account:preferences", kind="preferences", label="계획 선호 설정",
                               detail=compact(asdict(preferences)), basis="USER_REPORTED", href="/myinfo"))
        links.append(edge(root, "account:preferences", "PLANNED_WITH", "APPLICATION_RECORD"))
        today = datetime.now(ZoneInfo(goal.timezone if goal else "Asia/Seoul")).date()
        warnings = []
        matching_available = True
        try:
            self.skills.snapshots()
        except M5DataUnavailableError:
            matching_available = False
            warnings.append("SKILL_CATALOG_UNAVAILABLE")
        active = [item for item in capabilities if item.lifecycle == "ACTIVE"]
        selected = ranked(active, query, lambda item: item.raw_text + compact(item.details), 12)
        for item in selected:
            matching = self.skills.matching(item, goal.occupation_id if goal else None, today) if matching_available else None
            node_id = f"spec:{item.capability_id}"
            nodes.append(GraphNode(id=node_id, kind="capability", label=item.raw_text[:120],
                                   detail=compact({"matching": matching.model_dump() if matching else {
                                       "status": "UNAVAILABLE", "credit": None, "message": "역량 카탈로그를 확인하지 못해 현재 반영치를 계산할 수 없어요."}, "category": item.category,
                                                   "proficiency": item.proficiency, "evidence": item.details}),
                                   basis="USER_REPORTED", href="/myinfo"))
            links.append(edge("account:profile", node_id, "REPORTED", "USER_REPORTED"))
            for skill in matching.competencies[:4] if matching else ():
                nodes.append(GraphNode(id=skill.entity_id,
                                       kind="ncs_competency" if skill.source == "NCS" else "onet_skill",
                                       label=skill.label, requirement_key=next(iter(skill.requirement_keys), None),
                                       detail=matching.message, basis="REFERENCE"))
                links.append(edge(node_id, skill.entity_id, "MATCHED_TO", "APPLICATION_RECORD",
                                  f"{matching.status} · 계획 반영치 {matching.credit:g}. {matching.message}"))
        if goal:
            dashboard = await self.m5.get_dashboard(user_id, goal_id)
            if dashboard.analysis:
                analysis = await self.queries.get_analysis(user_id, dashboard.analysis.analysis_id)
                if analysis.goal_id != goal_id:
                    raise MissingRecordError(resource="analysis")
                analysis_id = f"analysis:{analysis.analysis_id}"
                stale = analysis.profile_version != profile.profile_version
                if stale:
                    warnings.append("ANALYSIS_PROFILE_OUTDATED")
                nodes.append(GraphNode(id=analysis_id, kind="analysis", label="최근 역량 분석",
                                       detail=compact({"status": analysis.status, "profile_version": analysis.profile_version,
                                                       "current_profile_version": profile.profile_version,
                                                       "reference_at": analysis.reference_at, "release_id": analysis.release_id}),
                                       source_pin=analysis.release_id, basis="APPLICATION_RECORD", href=f"/analysis?goal_id={goal_id}"))
                links.append(edge(root, analysis_id, "ANALYZED_BY", "APPLICATION_RECORD"))
                lookup = self.metadata if getattr(self.metadata, "release_id", None) == analysis.release_id else None
                summary = build_analysis_summary(analysis.results, lookup, include_satisfied=True)
                if summary:
                    for skill in ranked(summary.skills, query, lambda item: item.name, 20):
                        key = f"requirement:{skill.requirement_key}"
                        nodes.append(GraphNode(id=key, kind="ncs_competency", label=skill.name,
                                               detail=compact(asdict(skill)), basis="REVIEWED_REQUIREMENT",
                                               requirement_key=skill.requirement_key, ncs_base_code=skill.ncs_base_code,
                                               source_pin=analysis.release_id, href=f"/analysis?goal_id={goal_id}"))
                        links.append(edge(analysis_id, key, skill.status, "APPLICATION_RECORD"))
                        if skill.ncs_base_code:
                            catalog_id = f"ncs:unit:{skill.ncs_base_code}"
                            if any(node.id == catalog_id for node in nodes):
                                links.append(edge(catalog_id, key, "EVALUATED_AS", "APPLICATION_RECORD"))
            else:
                warnings.append("NO_ANALYSIS")
            roadmaps = await self.queries.list_roadmaps(user_id)
            roadmap = next((r for r in roadmaps if r.goal_id == goal_id and r.state == "ACTIVE"), None)
            if roadmap:
                roadmap = await self.queries.get_roadmap(user_id, roadmap.roadmap_id)
                if roadmap.goal_id != goal_id:
                    raise MissingRecordError(resource="roadmap")
                overrides = await self.weekly.get(user_id, roadmap.roadmap_id) if self.weekly else None
                selected_steps = ranked(roadmap.steps, query, lambda item: (item.title or "") + compact(item.outcomes), 16)
                ids = {step.step_id for step in selected_steps}
                for step in selected_steps:
                    key = f"step:{step.step_id}"
                    saved = overrides.step_schedules.get(str(step.step_id)) if overrides and overrides.is_customized else None
                    schedule = {"state": step.state,
                                "current_start": saved.start_date if saved else step.planned_start,
                                "current_end": saved.end_date if saved else step.planned_end,
                                "schedule_source": "CUSTOM" if saved else "SOLVER",
                                "hours": saved.hours if saved else None,
                                "weekly_plan_version": overrides.version if overrides else None}
                    nodes.append(GraphNode(id=key, kind="activity", label=step.title or step.action_id,
                                           detail=compact(schedule | asdict(step) | {"roadmap_version": roadmap.roadmap_version,
                                                                         "profile_version": roadmap.profile_version}),
                                           basis="APPLICATION_RECORD", href=f"/roadmap?goal_id={goal_id}&step={step.step_id}"))
                    links.append(edge(root, key, "PLANNED_ACTIVITY", "APPLICATION_RECORD"))
                    for prerequisite in step.prerequisite_step_ids:
                        if prerequisite in ids:
                            links.append(edge(f"step:{prerequisite}", key, "PREREQUISITE", "APPLICATION_RECORD"))
                    for outcome in step.outcomes:
                        req = outcome.get("requirement_key") if isinstance(outcome, Mapping) else None
                        if req and any(n.id == f"requirement:{req}" for n in nodes):
                            links.append(edge(key, f"requirement:{req}", "DEVELOPS", "APPLICATION_RECORD"))
            else:
                warnings.append("NO_ROADMAP")
        graph = EvidenceGraph(nodes=tuple({node.id: node for node in nodes}.values()), edges=tuple(links),
                              truncated=len(active) > 12 or len(goals) > 6)
        return WorkspaceEvidence(goal_id=goal_id, occupation_id=goal.occupation_id if goal else None,
                                 graph=graph, sources=sources_for(graph), warnings=tuple(warnings))
