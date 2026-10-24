import base64
import json
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from test_acceptance_m5_worker import CAPABILITY_ENTITY_ID, CAPABILITY_LABEL, _snapshot

from jobtology_be.api.chat import require_chat_rate_limiter
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import CapabilityView, GoalView
from jobtology_be.chat.service import (
    AllowedCapability,
    ChatCapabilityService,
    allowed_capabilities,
    select_candidates,
    select_suggestions,
)
from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.infrastructure.persistence.rate_limit import RateLimitDecision
from jobtology_be.llm.client import ChatGptOAuthClient, LlmMessage, LlmUnavailableError
from jobtology_be.llm.credentials import (
    CredentialStoreError,
    FileCredentialStore,
    OAuthCredentials,
    generate_encryption_key,
    parse_encryption_key,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

USER_ID = UUID("5a0d8b4e-3f0e-4c3c-9a3e-0f9f7e2b1c11")
NOW = datetime.now(UTC)
ALLOWED = (
    AllowedCapability(code="2001020211", entity_id="ncs:unit:2001020211", label="서버프로그램 구현", hints=()),
    AllowedCapability(code="2001020413", entity_id="ncs:unit:2001020413", label="SQL활용", hints=()),
)


def test_select_candidates_keeps_only_allowed_codes_with_verbatim_user_quotes() -> None:
    raw = [
        {"code": "2001020211", "evidence_quote": "Spring Boot로  게시판 API를 만들었어요"},
        {"code": "9999999999", "evidence_quote": "Spring Boot로 게시판 API를 만들었어요"},
        {"code": "2001020413", "evidence_quote": "MySQL로 복잡한 쿼리를 최적화했습니다"},
        {"code": "2001020211", "evidence_quote": "게시판 API"},
    ]

    selected = select_candidates(raw, ALLOWED, ["저는 Spring Boot로 게시판 API를 만들었어요."])

    assert [item.entity_id for item in selected] == ["ncs:unit:2001020211"]
    assert selected[0].label == "서버프로그램 구현"


def test_select_candidates_ignores_malformed_model_output() -> None:
    assert select_candidates("nope", ALLOWED, ["x"]) == ()
    assert select_candidates([{"code": 1}, "x", {"evidence_quote": "x"}], ALLOWED, ["x"]) == ()


def test_select_suggestions_trims_dedupes_and_bounds_model_questions() -> None:
    raw = ["  Docker는 어떻게 시작해?  ", "Docker는 어떻게 시작해?", "", 3, "x" * 61,
           "정보처리산업기사 따야 해?", "포트폴리오 뭐부터 만들까?", "SQL 공부 순서 알려줘", "다섯 번째"]

    assert select_suggestions(raw) == (
        "Docker는 어떻게 시작해?", "정보처리산업기사 따야 해?", "포트폴리오 뭐부터 만들까?",
    )
    assert select_suggestions(None) == ()


def test_suggestions_reject_assistant_advice_and_preserve_user_requests() -> None:
    assert select_suggestions([
        "등록한 자격·프로젝트에 구체적인 역량과 20자 이상의 경험 설명을 추가한 뒤 분석을 다시 실행하세요.",
        "먼저 데이터베이스 구현과 애플리케이션 배포를 실제 프로젝트로 보완하세요.",
        "내 프로젝트에서 어떤 근거를 더 설명하면 좋을까?", "SQL 공부 순서 알려줘",
    ]) == ("내 프로젝트에서 어떤 근거를 더 설명하면 좋을까?", "SQL 공부 순서 알려줘")


def test_suggestions_preserve_polite_user_requests() -> None:
    assert select_suggestions([
        "SQL 공부 순서 알려주세요", "프로젝트를 설명해 주세요.", "다음 단계를 추천해줘요",
    ]) == ("SQL 공부 순서 알려주세요", "프로젝트를 설명해 주세요.", "다음 단계를 추천해줘요")


def test_allowed_capabilities_excludes_requirements_the_user_already_has() -> None:
    snapshot = _snapshot()

    assert [item.label for item in allowed_capabilities(snapshot, frozenset())] == [CAPABILITY_LABEL]
    assert allowed_capabilities(snapshot, frozenset({CAPABILITY_ENTITY_ID})) == ()
    assert allowed_capabilities(snapshot, frozenset({CAPABILITY_LABEL.upper()}))


class FakeQueries:
    def __init__(self, occupation_id: str | None) -> None:
        self.occupation_id = occupation_id

    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]:
        return (
            GoalView(
                goal_id=USER_ID, goal_mode="TARGETED", occupation_id=self.occupation_id, target_by=NOW,
                timezone="UTC", original_time_phrase="6개월", status="ACTIVE", created_at=NOW, updated_at=NOW,
            ),
        )

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]:
        return ()


class FakeLlm:
    def __init__(self, response: dict[str, JsonValue]) -> None:
        self.response = response
        self.instructions = ""

    async def complete_json(
        self, *, instructions: str, messages: Sequence[LlmMessage], schema_name: str,
        schema: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self.instructions = instructions
        return self.response


def _service(llm: FakeLlm, occupation_id: str | None = "BACKEND_DEVELOPER") -> ChatCapabilityService:
    return ChatCapabilityService(
        llm=llm, queries=FakeQueries(occupation_id),
        snapshots=lambda: (_snapshot(),), display_name=lambda _: "백엔드 개발자",
    )


def test_service_lists_only_the_active_roles_requirements_and_filters_the_reply() -> None:
    code = CAPABILITY_ENTITY_ID.rsplit(":", 1)[-1]
    llm = FakeLlm({"reply": "좋아요!", "candidates": [{"code": code, "evidence_quote": "API를 배포했어요"}]})

    result = anyio.run(_service(llm).respond, USER_ID, (LlmMessage("user", "Python으로 API를 배포했어요"),))

    assert f"- {code}: {CAPABILITY_LABEL}" in llm.instructions
    assert "백엔드 개발자" in llm.instructions
    assert result.reply == "좋아요!"
    assert [item.label for item in result.candidates] == [CAPABILITY_LABEL]


def test_service_offers_no_candidates_without_an_active_targeted_goal() -> None:
    llm = FakeLlm({"reply": "직무를 먼저 정해볼까요?", "candidates": [{"code": "x", "evidence_quote": "y"}]})

    result = anyio.run(_service(llm, None).respond, USER_ID, (LlmMessage("user", "y"),))

    assert "(없음" in llm.instructions
    assert result.candidates == ()
    assert result.occupation_id is None


def test_explicit_goal_overrides_the_first_active_goal_and_rejects_foreign_ids():
    from dataclasses import replace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
    llm = FakeLlm({"reply": "좋아요", "candidates": [], "suggestions": []})
    service = _service(llm)
    goal = anyio.run(service.queries.list_goals, USER_ID)[0]
    selected = uuid4()
    queries = type("Queries", (), {
        "list_goals": AsyncMock(return_value=(replace(goal, occupation_id="OTHER_ROLE"), replace(goal, goal_id=selected))),
        "list_capabilities": AsyncMock(return_value=()),
    })()
    service = replace(service, queries=queries)
    result = anyio.run(service.respond, USER_ID, (LlmMessage("user", "내 목표"),), "counsel", selected)
    assert result.occupation_id == "BACKEND_DEVELOPER"
    with pytest.raises(MissingRecordError):
        anyio.run(service.respond, USER_ID, (LlmMessage("user", "내 목표"),), "counsel", uuid4())


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class AllowChatLimiter:
    async def consume(self, user_id: UUID) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, retry_after_seconds=60)


def test_chat_api_reports_unavailable_and_returns_503_without_a_configured_llm() -> None:
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(identity_provider=StaticIdentityProvider()))
    with TestClient(app) as client:
        status = client.get("/api/v1/chat/status")
        sent = client.post("/api/v1/chat/messages", json={"messages": [{"role": "user", "text": "안녕"}]})

    assert status.json() == {"available": False}
    assert sent.status_code == 503


def test_chat_api_returns_reply_and_candidates_and_validates_the_conversation() -> None:
    code = CAPABILITY_ENTITY_ID.rsplit(":", 1)[-1]
    llm = FakeLlm({
        "reply": "멋져요", "candidates": [{"code": code, "evidence_quote": "API를 배포했어요"}],
        "suggestions": ["배포 자동화는 어떻게 해?"],
    })
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(), chat_service=_service(llm)),
    )
    app.dependency_overrides[require_chat_rate_limiter] = lambda: AllowChatLimiter()
    with TestClient(app) as client:
        ok = client.post("/api/v1/chat/messages", json={"messages": [
            {"role": "assistant", "text": "무엇을 해봤나요?"}, {"role": "user", "text": "API를 배포했어요"},
        ]})
        bad = client.post("/api/v1/chat/messages", json={"messages": [{"role": "assistant", "text": "hi"}]})

    assert ok.status_code == 200
    assert ok.json() == {
        "reply": "멋져요",
        "occupation_id": "BACKEND_DEVELOPER",
        "candidates": [{"entity_id": CAPABILITY_ENTITY_ID, "label": CAPABILITY_LABEL, "evidence_quote": "API를 배포했어요", "category": None}],
        "suggestions": ["배포 자동화는 어떻게 해?"],
    }
    assert bad.status_code == 422


def _token(expires_in: float) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + expires_in}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


def test_oauth_client_rejects_expired_tokens_without_refreshing(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": _token(-10), "account_id": "a", "refresh_token": "r"}}))
    client = ChatGptOAuthClient(store=FileCredentialStore(auth), model="m")

    with pytest.raises(LlmUnavailableError):
        anyio.run(client.credentials)


def test_oauth_client_reads_valid_credentials_and_reports_unreadable_files(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    token = _token(3600)
    auth.write_text(json.dumps({"tokens": {"access_token": token, "account_id": "acct", "refresh_token": "r"}}))

    assert anyio.run(ChatGptOAuthClient(store=FileCredentialStore(auth), model="m").credentials) == (token, "acct")
    with pytest.raises(LlmUnavailableError):
        anyio.run(ChatGptOAuthClient(store=FileCredentialStore(tmp_path / "missing.json"), model="m").credentials)


class MemoryStore:
    def __init__(self, credentials: OAuthCredentials | None) -> None:
        self.credentials = credentials
        self.saved: list[OAuthCredentials] = []

    async def load(self) -> OAuthCredentials | None:
        return self.credentials

    async def save(self, credentials: OAuthCredentials) -> None:
        self.credentials = credentials
        self.saved.append(credentials)


def test_oauth_client_refreshes_an_expiring_token_and_persists_the_rotation() -> None:
    fresh = _token(3600)
    store = MemoryStore(OAuthCredentials.model_validate(
        {"tokens": {"access_token": _token(60), "account_id": "acct", "refresh_token": "old"}, "auth_mode": "chatgpt"}
    ))
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"access_token": fresh, "refresh_token": "new"})

    client = ChatGptOAuthClient(
        store=store, model="m", refresh_enabled=True, http_transport=httpx.MockTransport(handler)
    )

    assert anyio.run(client.credentials) == (fresh, "acct")
    assert requests[0]["grant_type"] == "refresh_token"
    assert requests[0]["refresh_token"] == "old"
    assert store.saved[0].tokens.refresh_token == "new"
    assert store.saved[0].model_dump()["auth_mode"] == "chatgpt"


def test_oauth_client_reports_missing_credentials() -> None:
    with pytest.raises(LlmUnavailableError):
        anyio.run(ChatGptOAuthClient(store=MemoryStore(None), model="m").credentials)


def test_encryption_keys_must_be_32_bytes_of_base64() -> None:
    assert len(parse_encryption_key(generate_encryption_key())) == 32
    for bad in ("short", "!!!not-base64!!!"):
        with pytest.raises(CredentialStoreError):
            parse_encryption_key(bad)


def test_onboarding_mode_asks_about_uncovered_capabilities_instead_of_counseling() -> None:
    llm = FakeLlm({"reply": "다음 질문", "candidates": []})

    anyio.run(lambda: _service(llm).respond(USER_ID, (LlmMessage("user", "시작할게요"),), "onboarding"))

    assert "온보딩 질문" in llm.instructions
    assert "상담 답변" not in llm.instructions


def test_quotes_must_preserve_case_whitespace_and_punctuation_exactly():
    spoken = 'Spring Boot로  API를 만들었습니다.'
    for changed in ('spring Boot로  API를 만들었습니다.', 'Spring Boot로 API를 만들었습니다.',
                    'Spring Boot로  API를 만들었습니다!'):
        assert select_candidates([{'code': ALLOWED[0].code, 'evidence_quote': changed}], ALLOWED, [spoken]) == ()
    selected = select_candidates([{'code': ALLOWED[0].code, 'evidence_quote': spoken}], ALLOWED, [spoken])
    assert selected[0].evidence_quote == spoken


@pytest.mark.parametrize('category,proficiency,details,suppressed', [
    ('skill', 'PRODUCTION', {}, False),
    ('skill', 'PRODUCTION', {'competency_ids': ['ncs:unit:2001020211'], 'evidence': '짧음'}, False),
    ('skill', 'GUIDED', {'competency_ids': ['ncs:unit:2001020211'], 'evidence': 'API를 직접 구현하고 배포한 결과물과 상세 역할을 설명합니다.'}, False),
    ('skill', 'PRODUCTION', {'competency_ids': ['ncs:unit:2001020211'], 'evidence': 'API를 직접 구현하고 배포한 결과물과 상세 역할을 설명합니다.'}, True),
    ('certification', 'PRODUCTION', {'competency_ids': ['ncs:unit:2001020211'], 'evidence': '자격증 취득 과정에서 API 구현 및 배포를 실습했습니다.', 'expires_on': '2020-01-01'}, False),
])
def test_chat_ownership_uses_the_same_credit_rules_as_analysis(category, proficiency, details, suppressed):
    from dataclasses import replace

    snapshot = _snapshot()
    entity_id = 'ncs:unit:2001020211'
    snapshot = replace(snapshot, requirements=tuple(replace(item, entity_id=entity_id)
                                                   for item in snapshot.requirements))
    capability = CapabilityView(USER_ID, category, CAPABILITY_LABEL, entity_id, proficiency,
                                'SELF_REPORTED', 'ACTIVE', details, None, NOW, NOW)

    class Queries(FakeQueries):
        async def list_capabilities(self, user_id):
            return (capability,)

    llm = FakeLlm({'reply': '확인했어요.', 'candidates': [], 'suggestions': []})
    service = ChatCapabilityService(llm, Queries('BACKEND_DEVELOPER'), lambda: (snapshot,), lambda _: '백엔드')
    anyio.run(service.respond, USER_ID, (LlmMessage('user', '내 기록을 확인해 주세요.'),))
    assert ('- 2001020211:' not in llm.instructions) is suppressed
