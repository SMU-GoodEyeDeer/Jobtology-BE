from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

type DateText = str


class LiveRead(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class LiveSource(LiveRead):
    source_id: str
    run_id: str
    data_as_of: str | None


class NcsCategory(LiveRead):
    code: str | None
    name: str | None


class PostingItem(LiveRead):
    posting_id: str
    title: str | None
    organization_code: str | None
    organization_name: str | None
    date_posted: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    closing_date: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    ongoing: bool | None
    regions: list[str]
    employment_types: list[str]
    recruitment_type: str | None
    education: str | None
    ncs_categories: list[NcsCategory]
    headcount: int | None
    source_url: str | None


class PostingFilters(LiveRead):
    q: str | None
    ncs_category: str | None
    region: str | None
    open_on: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class LiveResponse(LiveRead):
    contract_version: Literal["hop-live-source-v1"]
    sources: list[LiveSource]


class PostingsResponse(LiveResponse):
    filters: PostingFilters
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    items: list[PostingItem]


class PostingResponse(LiveResponse):
    item: PostingItem


class ExamDates(LiveRead):
    registration_start: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    registration_end: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    exam_start: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    exam_end: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    result_date: DateText | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ExamSession(LiveRead):
    qualification_code: str
    qualification_name: str | None
    year: int
    round: int
    category_code: str | None
    name: str | None
    written: ExamDates
    practical: ExamDates


class ExamFilters(LiveRead):
    qualification: str | None
    from_date: DateText | None = Field(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")
    to_date: DateText | None = Field(alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$")


class ExamSessionsResponse(LiveResponse):
    filters: ExamFilters
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    items: list[ExamSession]


class NcsDemandSource(LiveRead):
    source_id: Literal["link_publication"]
    publication_id: str
    created_at: str
    posting_source: str
    run_id: str
    is_latest_publication: bool


class NcsDemandReview(LiveRead):
    link_reviewer_kinds: dict[str, int]


class NcsDemandFilters(LiveRead):
    ncs_prefix: str | None


class NcsDemandEvidence(LiveRead):
    source_id: str
    publication_id: str
    created_at: str
    source_posting_id: str
    title: str | None
    position: str | None
    duty: str | None


class RelatedQualification(LiveRead):
    qualification_code: str
    qualification_name: str | None


class NcsDemandItem(LiveRead):
    competency_code: str
    competency_name: str | None
    ncs_occupation_code: str
    ncs_occupation_name: str | None
    postings: int = Field(ge=0)
    links: int = Field(ge=0)
    evidence: list[NcsDemandEvidence]
    related_qualifications: list[RelatedQualification]


class NcsDemandResponse(LiveRead):
    contract_version: Literal["hop-live-ncs-demand-v1"]
    sources: list[NcsDemandSource]
    review: NcsDemandReview
    filters: NcsDemandFilters
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    items: list[NcsDemandItem]
