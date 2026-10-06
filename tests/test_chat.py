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

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.queries import CapabilityView, GoalView
from jobtology_be.chat.service import (
    AllowedCapability,
    ChatCapabilityService,
    allowed_capabilities,
    select_candidates,
)
from jobtology_be.infrastructure.persistence.contracts import JsonValue
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


def test_allowed_capabilities_excludes_requirements_the_user_already_has() -> None:
    snapshot = _snapshot()

    assert [item.label for item in allowed_capabilities(snapshot, frozenset())] == [CAPABILITY_LABEL]
    assert allowed_capabilities(snapshot, frozenset({CAPABILITY_LABEL.upper()})) == ()


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


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


def test_chat_api_reports_unavailable_and_returns_503_without_a_configured_llm() -> None:
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(identity_provider=StaticIdentityProvider()))
    with TestClient(app) as client:
        status = client.get("/api/v1/chat/status")
        sent = client.post("/api/v1/chat/messages", json={"messages": [{"role": "user", "text": "안녕"}]})

    assert status.json() == {"available": False}
    assert sent.status_code == 503


def test_chat_api_returns_reply_and_candidates_and_validates_the_conversation() -> None:
    code = CAPABILITY_ENTITY_ID.rsplit(":", 1)[-1]
    llm = FakeLlm({"reply": "멋져요", "candidates": [{"code": code, "evidence_quote": "API를 배포했어요"}]})
    app = create_app(
        Settings(enable_fixtures=False),
        dependencies=ApiDependencies(identity_provider=StaticIdentityProvider(), chat_service=_service(llm)),
    )
    with TestClient(app) as client:
        ok = client.post("/api/v1/chat/messages", json={"messages": [
            {"role": "assistant", "text": "무엇을 해봤나요?"}, {"role": "user", "text": "API를 배포했어요"},
        ]})
        bad = client.post("/api/v1/chat/messages", json={"messages": [{"role": "assistant", "text": "hi"}]})

    assert ok.status_code == 200
    assert ok.json() == {
        "reply": "멋져요",
        "occupation_id": "BACKEND_DEVELOPER",
        "candidates": [{"entity_id": CAPABILITY_ENTITY_ID, "label": CAPABILITY_LABEL, "evidence_quote": "API를 배포했어요"}],
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
