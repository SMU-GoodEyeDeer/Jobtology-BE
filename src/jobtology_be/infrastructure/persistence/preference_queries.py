from uuid import UUID

from sqlalchemy import select

from jobtology_be.application.services.preferences import RoutePreferences
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import profiles, route_preferences


class PostgresRoutePreferencesQuery:
    _database: Database

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_route_preferences(self, user_id: UUID) -> RoutePreferences:
        async with self._database.sessions() as session:
            row = (
                await session.execute(
                    select(
                        profiles.c.version,
                        route_preferences.c.available_hours_per_week,
                        route_preferences.c.availability_source,
                        route_preferences.c.budget_mode,
                        route_preferences.c.max_out_of_pocket_krw,
                        route_preferences.c.fastest_path,
                        route_preferences.c.needs_portfolio,
                        route_preferences.c.career_switch,
                    )
                    .join(route_preferences, route_preferences.c.user_id == profiles.c.user_id)
                    .where(profiles.c.user_id == user_id)
                )
            ).one_or_none()
        if row is None:
            raise MissingRecordError(resource="route preferences")
        return RoutePreferences(
            profile_version=row.version,
            available_hours_per_week=row.available_hours_per_week,
            availability_source=row.availability_source,
            budget_mode=row.budget_mode,
            max_out_of_pocket_krw=row.max_out_of_pocket_krw,
            fastest_path=row.fastest_path,
            needs_portfolio=row.needs_portfolio,
            career_switch=row.career_switch,
        )
