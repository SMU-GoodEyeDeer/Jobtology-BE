from typing import Annotated, ClassVar, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.application.specs import SpecCategory
from jobtology_be.chat.service import ChatCapabilityService
from jobtology_be.infrastructure.persistence.rate_limit import (
    AccountRateLimiter,
    RateLimitUnavailableError,
)
from jobtology_be.llm.client import LlmMessage, LlmUnavailableError

router = APIRouter(tags=["chat"])

class ChatMessageIn(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    text: StrictStr = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ChatMessageIn, ...] = Field(min_length=1, max_length=20)
    mode: Literal["counsel", "onboarding", "spec"] = "counsel"

    @field_validator("messages")
    @classmethod
    def _ends_with_user(cls, value: tuple[ChatMessageIn, ...]) -> tuple[ChatMessageIn, ...]:
        if value[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return value


class ChatCandidateOut(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    label: str
    evidence_quote: str
    category: SpecCategory | None = None


class ChatResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    reply: str
    occupation_id: str | None
    candidates: tuple[ChatCandidateOut, ...]
    suggestions: tuple[str, ...] = ()


class ChatStatusResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    available: bool


def require_chat_service() -> ChatCapabilityService | None:
    return None


def require_chat_rate_limiter() -> AccountRateLimiter | None:
    return None


@router.get(
    "/chat/status",
    response_model=ChatStatusResponse,
    summary="Chat availability",
    description="Reports whether the AI chat is configured. Clients fall back when unavailable.",
)
async def chat_status(
    service: Annotated[ChatCapabilityService | None, Depends(require_chat_service)],
) -> ChatStatusResponse:
    return ChatStatusResponse(available=service is not None)


@router.post(
    "/chat/messages",
    response_model=ChatResponse,
    summary="Send chat messages",
    description=(
        "Stateless: the client sends the recent conversation and the server does not store it. "
        "Returns a counseling reply, up to four follow-up questions the user may ask next "
        "(`suggestions`), and capability candidates restricted to the active goal's "
        "approved requirements, each with a verbatim user quote. Candidates are not saved; "
        "after the user confirms, save one with `POST /api/v1/me/capabilities` "
        "(`category: \"chat\"`, `raw_text`: candidate label, `details.evidence_quote`)."
    ),
)
async def send_chat_messages(
    request: ChatRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ChatCapabilityService | None, Depends(require_chat_service)],
    rate_limiter: Annotated[AccountRateLimiter | None, Depends(require_chat_rate_limiter)],
) -> ChatResponse:
    if service is None or rate_limiter is None:
        raise HTTPException(status_code=503)
    try:
        decision = await rate_limiter.consume(principal.user_id)
    except RateLimitUnavailableError as error:
        raise HTTPException(status_code=503) from error
    if not decision.allowed:
        raise HTTPException(
            status_code=429, headers={"Retry-After": str(decision.retry_after_seconds)}
        )
    try:
        result = await service.respond(
            principal.user_id,
            tuple(LlmMessage(role=message.role, text=message.text) for message in request.messages),
            request.mode,
        )
    except LlmUnavailableError as error:
        raise HTTPException(status_code=503) from error
    return ChatResponse(
        reply=result.reply,
        occupation_id=result.occupation_id,
        candidates=tuple(
            ChatCandidateOut(
                entity_id=item.entity_id, label=item.label, evidence_quote=item.evidence_quote,
                category=item.category,
            )
            for item in result.candidates
        ),
        suggestions=result.suggestions,
    )
