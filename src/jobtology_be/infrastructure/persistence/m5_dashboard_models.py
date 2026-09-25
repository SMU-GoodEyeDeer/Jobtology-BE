from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from jobtology_be.application.m5_queries import DashboardRoadmapView
from jobtology_be.infrastructure.persistence.contracts import JsonValue


@dataclass(frozen=True, slots=True)
class GoalRow:
    id: UUID


@dataclass(frozen=True, slots=True)
class ActiveRoadmapRow:
    view: DashboardRoadmapView
    validity: Mapping[str, JsonValue]
