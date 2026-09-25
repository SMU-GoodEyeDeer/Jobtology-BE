from hashlib import sha256
from typing import ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)


class _SourceRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )


class Neo4jPublication(_SourceRecord):
    id: StrictStr = Field(min_length=1)
    publication_id: StrictStr = Field(min_length=1)
    postings: StrictInt
    state: StrictStr = Field(min_length=1)


class PayloadHashMismatchError(ValueError):
    def __str__(self) -> str:
        return "source payload hash mismatch"


class PayloadShapeMismatchError(ValueError):
    def __str__(self) -> str:
        return "source payload shape mismatch"


class _SafeExtraction(_SourceRecord):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", frozen=True, hide_input_in_errors=True
    )

    duties_status: StrictStr
    extraction_scope: StrictStr
    schema_version: StrictStr


class _SafePayloadIdentity(_SourceRecord):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", frozen=True, hide_input_in_errors=True
    )

    item_id: StrictStr
    posting_id: StrictStr
    revision_id: StrictStr
    source_hash: StrictStr
    extraction: _SafeExtraction


class Neo4jEnrichment(_SourceRecord):
    id: StrictStr = Field(min_length=1)
    publication_id: StrictStr = Field(min_length=1)
    posting_id: StrictStr = Field(min_length=1)
    current: StrictBool
    managed_by: StrictStr = Field(exclude=True, repr=False)
    name: StrictStr = Field(exclude=True, repr=False)
    payload_json: StrictStr = Field(exclude=True, repr=False)
    payload_hash: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def verify_payload_hash(self) -> "Neo4jEnrichment":
        if sha256(self.payload_json.encode("utf-8")).hexdigest() != self.payload_hash:
            raise PayloadHashMismatchError()
        try:
            _SafePayloadIdentity.model_validate_json(self.payload_json)
        except ValidationError as error:
            raise PayloadShapeMismatchError() from error
        return self


class Neo4jCatalogNode(_SourceRecord):
    id: StrictStr = Field(min_length=1)
    code: StrictStr = Field(min_length=1)
    kind: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)
    name_source_record_id: StrictStr = Field(exclude=True, repr=False)
    name_source_run_id: StrictStr = Field(exclude=True, repr=False)


class Neo4jOccupationNode(Neo4jCatalogNode):
    pass


class Neo4jNcsCompetencyNode(Neo4jCatalogNode):
    pass


class Neo4jNcsAlignment(_SourceRecord):
    accepted: StrictBool
    decision_id: StrictInt
    publication_id: StrictStr = Field(min_length=1)
