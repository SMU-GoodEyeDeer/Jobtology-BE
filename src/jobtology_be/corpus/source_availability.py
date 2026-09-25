from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class _CapabilityModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )


class Available(_CapabilityModel):
    status: Literal["AVAILABLE"]


class Unavailable(_CapabilityModel):
    status: Literal["UNAVAILABLE"]
    reason: Literal[
        "SOURCE_UNAVAILABLE",
        "UNVERIFIED_SOURCE_CONTRACT",
        "MISSING_REVIEWED_REQUIREMENTS",
        "MISSING_ACTIVITY_TEMPLATES",
    ]


type CapabilityAvailability = Annotated[Available | Unavailable, Field(discriminator="status")]


class SourceCapabilities(_CapabilityModel):
    source: Literal["local_json", "neo4j_query_api"]
    catalog: CapabilityAvailability
    editorial_analysis: CapabilityAvailability
    route_planning: CapabilityAvailability
