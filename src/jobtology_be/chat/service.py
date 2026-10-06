import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from jobtology_be.application.queries import CapabilityView, GoalView
from jobtology_be.corpus.snapshot import PublishedCorpusSnapshot
from jobtology_be.infrastructure.persistence.contracts import JsonValue
from jobtology_be.llm.client import LlmClient, LlmMessage

MAX_CANDIDATES: Final = 3
MAX_QUOTE_LENGTH: Final = 300

INSTRUCTIONS: Final = """\
너는 대학생의 IT 진로를 돕는 커리어 상담가다. 한국어로 짧고 친근하게(3문장 이내) 답한다.

역할 1 — 상담 답변(reply): 사용자의 질문에 답하고, 사용자가 이미 해 본 경험을 더 자세히 말하도록 \
구체적인 질문을 하나 덧붙인다. 확실하지 않은 사실(채용 통계, 연봉 등)은 지어내지 않는다.

역할 2 — 역량 후보(candidates): 사용자가 **이미 직접 해 본 경험**을 말했을 때만, 아래 [허용 역량 목록]에서 \
해당하는 역량의 code를 고른다.
- 목록에 없는 역량은 절대 만들지 않는다. 애매하면 고르지 않는다.
- 계획·희망·관심("배우고 싶어요", "해볼 예정")이나 남의 경험, 단순히 들어본 것은 근거가 아니다.
- 역량명이 뜻하는 일을 사용자가 직접 했다고 말한 경우만 고른다. 어떤 기술을 쓰거나 연결했다는 말만으로 그 기술의 설계·구현·운영 역량까지 넓히지 않는다(예: "DB에 저장했다"는 데이터베이스 구현이 아니다).
- evidence_quote에는 근거가 되는 사용자 발화를 **글자 그대로** 짧게 복사한다(요약·수정 금지).
- 최대 3개. 해당 없으면 빈 배열.

[목표 직무] {occupation}
[허용 역량 목록] (code: 역량명 — 관련 기술 예시)
{allowed}
"""

SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["code", "evidence_quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reply", "candidates"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class AllowedCapability:
    code: str
    entity_id: str
    label: str
    hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChatCandidate:
    entity_id: str
    label: str
    evidence_quote: str


@dataclass(frozen=True, slots=True)
class ChatReply:
    reply: str
    occupation_id: str | None
    candidates: tuple[ChatCandidate, ...]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def allowed_capabilities(
    snapshot: PublishedCorpusSnapshot, owned_raw_texts: frozenset[str]
) -> tuple[AllowedCapability, ...]:
    aliases = {entry.entity_id: entry.aliases for entry in snapshot.capability_entries}
    owned = {_normalize(text) for text in owned_raw_texts}
    allowed: list[AllowedCapability] = []
    for requirement in snapshot.requirements:
        entity_aliases = aliases.get(requirement.entity_id, frozenset())
        if any(_normalize(alias) in owned for alias in entity_aliases | {requirement.label}):
            continue
        code = requirement.entity_id.rsplit(":", 1)[-1]
        hints = tuple(
            sorted(
                alias
                for alias in entity_aliases
                if alias not in {code, requirement.label} and not alias.isdigit()
            )
        )
        allowed.append(
            AllowedCapability(
                code=code, entity_id=requirement.entity_id, label=requirement.label, hints=hints
            )
        )
    return tuple(allowed)


def select_candidates(
    raw: object, allowed: Sequence[AllowedCapability], user_texts: Sequence[str]
) -> tuple[ChatCandidate, ...]:
    """Keep only model picks that name an allowed code and quote the user verbatim."""
    if not isinstance(raw, list):
        return ()
    by_code = {item.code: item for item in allowed}
    spoken = [_normalize(text) for text in user_texts]
    selected: dict[str, ChatCandidate] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        code, quote = item.get("code"), item.get("evidence_quote")
        if not isinstance(code, str) or not isinstance(quote, str):
            continue
        capability = by_code.get(code.strip())
        normalized_quote = _normalize(quote)
        if (
            capability is None
            or not normalized_quote
            or len(quote) > MAX_QUOTE_LENGTH
            or not any(normalized_quote in text for text in spoken)
        ):
            continue
        selected.setdefault(
            capability.entity_id,
            ChatCandidate(
                entity_id=capability.entity_id, label=capability.label, evidence_quote=quote.strip()
            ),
        )
    return tuple(selected.values())[:MAX_CANDIDATES]


class ChatQueries(Protocol):
    async def list_goals(self, user_id: UUID) -> tuple[GoalView, ...]: ...

    async def list_capabilities(self, user_id: UUID) -> tuple[CapabilityView, ...]: ...


@dataclass(frozen=True, slots=True)
class ChatCapabilityService:
    llm: LlmClient
    queries: ChatQueries
    snapshots: Callable[[], tuple[PublishedCorpusSnapshot, ...]]
    display_name: Callable[[str], str | None]

    async def respond(self, user_id: UUID, messages: Sequence[LlmMessage]) -> ChatReply:
        goals = await self.queries.list_goals(user_id)
        occupation_id = next(
            (goal.occupation_id for goal in goals if goal.status == "ACTIVE" and goal.occupation_id),
            None,
        )
        snapshot = next(
            (
                item
                for item in self.snapshots()
                if occupation_id is not None and item.occupation_id == occupation_id and not item.is_fixture
            ),
            None,
        )
        allowed: tuple[AllowedCapability, ...] = ()
        if snapshot is not None:
            owned = frozenset(
                capability.raw_text
                for capability in await self.queries.list_capabilities(user_id)
                if capability.lifecycle == "ACTIVE"
            )
            allowed = allowed_capabilities(snapshot, owned)
        instructions = INSTRUCTIONS.format(
            occupation=(self.display_name(occupation_id) if occupation_id else None) or "미정",
            allowed="\n".join(
                f"- {item.code}: {item.label}" + (f" — {', '.join(item.hints)}" if item.hints else "")
                for item in allowed
            )
            or "(없음 — 후보를 고르지 않는다)",
        )
        result = await self.llm.complete_json(
            instructions=instructions, messages=messages, schema_name="chat_reply", schema=SCHEMA
        )
        reply = result.get("reply")
        return ChatReply(
            reply=reply.strip() if isinstance(reply, str) else "",
            occupation_id=occupation_id if snapshot is not None else None,
            candidates=select_candidates(
                result.get("candidates"),
                allowed,
                [message.text for message in messages if message.role == "user"],
            ),
        )
