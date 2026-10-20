from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from jobtology_be.application.specs import SkillItem, SpecCategory

PARSER_REVISION = 'jobtology-resume-v1-7541e87'
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_TEXT = 50_000
MAX_BLOCKS = 500
MAX_BLOCK_TEXT = 1800
MAX_SUGGESTIONS = 30
PARSE_SECONDS = 60

type ResumeFormat = Literal['pdf', 'docx', 'hwpx', 'hwp', 'doc']


class ResumeBlock(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    block_id: str = Field(max_length=128)
    text: str = Field(max_length=MAX_BLOCK_TEXT)
    text_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    page: int | None = Field(default=None, ge=1, le=MAX_PAGES)


class ParsedResume(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    blocks: tuple[ResumeBlock, ...] = Field(max_length=MAX_BLOCKS)
    warnings: tuple[str, ...] = Field(max_length=30)
    skipped_pages: tuple[int, ...] = ()


class ResumeSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    suggestion_id: str
    category: SpecCategory = 'skill'
    title: str = Field(max_length=500)
    competencies: tuple[SkillItem, ...] = Field(max_length=12)
    block_id: str
    quote: str = Field(max_length=2000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    method: Literal['KEYWORD', 'AI_ASSISTED']
    score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    score_max: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class ResumeScan(ParsedResume):
    document_sha256: str
    parser_revision: str = PARSER_REVISION
    suggestions: tuple[ResumeSuggestion, ...] = Field(max_length=MAX_SUGGESTIONS)


class ResumeInterpretRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=2000)
    occupation_id: str | None = Field(default=None, max_length=128)


class AiPick(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    title: str = Field(min_length=1, max_length=500)
    category: SpecCategory
    competency_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    quote: str = Field(min_length=1, max_length=2000)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode='after')
    def span(self) -> Self:
        if self.end - self.start != len(self.quote):
            raise ValueError('quote length differs from span')
        return self


class ResumeInterpretResponse(BaseModel):
    suggestions: tuple[ResumeSuggestion, ...]
    rejected_count: int = 0
