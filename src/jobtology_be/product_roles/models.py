from typing import ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RoleModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)


class Approval(RoleModel):
    approved_by: str
    decisions: dict[str, str]


class RolePolicy(RoleModel):
    occupation_id: str
    name: str
    occupation_codes: tuple[str, ...]
    additional_base_codes: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]


class ProductRolePolicy(RoleModel):
    version: Literal["product-roles-v1"]
    rule_approval: Approval
    roles: tuple[RolePolicy, ...]

    @property
    def requested_occupation_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for role in self.roles for code in (
            *role.occupation_codes,
            *(base[:8] for base in role.additional_base_codes),
        )}))


class InputSource(RoleModel):
    source_id: str
    run_id: str | None = None
    publication_id: str | None = None
    data_as_of: str | None = None
    created_at: str | None = None
    posting_source: str | None = None
    is_latest_publication: bool | None = None


class InputUnit(RoleModel):
    code: str
    base_code: str
    name: str
    level: int | None
    occupation_code: str
    occupation_name: str | None


class InputQualification(RoleModel):
    competency_code: str
    qualification_code: str
    qualification_name: str | None
    minimum_training_hours: int | None
    total_training_hours: int | None


class InputEvidence(RoleModel):
    competency_code: str
    postings: int = Field(ge=0)
    links: int = Field(ge=0)
    publication_ids: tuple[str, ...] = ()


class InputLinkedPosting(RoleModel):
    posting_key: str = Field(min_length=1)
    competency_codes: tuple[str, ...]


class ProductRoleInputs(RoleModel):
    contract_version: Literal["jobtology-product-role-inputs-v1"]
    sources: tuple[InputSource, ...]
    units: tuple[InputUnit, ...]
    qualifications: tuple[InputQualification, ...]
    evidence: tuple[InputEvidence, ...]
    # Absent before DB migration 028; demand percentages then stay null.
    linked_postings: tuple[InputLinkedPosting, ...] | None = None


class ArtifactApproval(RoleModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1)
    reviewed_at: AwareDatetime
