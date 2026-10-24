from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest

from jobtology_be.application.services.preferences import PersistentPreferencesService
from jobtology_be.application.specs import SkillCatalog
from jobtology_be.chat.personal import PersonalRetriever
from jobtology_be.infrastructure.persistence.contracts import (
    GoalMutation,
    MissingRecordError,
    ProfileMutation,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.m5_queries import PostgresM5Queries
from jobtology_be.infrastructure.persistence.preference_queries import PostgresRoutePreferencesQuery
from jobtology_be.infrastructure.persistence.queries import PostgresProductQueries
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

pytest_plugins = ("test_acceptance_persistence",)


def test_rag_reads_real_account_data_and_rejects_another_accounts_goal(acceptance_database_url):
    async def run():
        database = Database.create(acceptance_database_url)
        store = PostgresApplicationStore(database)
        first, second = uuid4(), uuid4()
        try:
            own = None
            foreign = None
            for user, name in [(first, "own-profile-marker"), (second, "foreign-profile-marker")]:
                await store.create_user(UserCreate(user))
                await store.mutate_profile(ProfileMutation(user, 1, name, None, 3, "ENROLLED", None))
                goal = await store.mutate_goal(GoalMutation(user, 2, None, "TARGETED", "BACKEND_DEVELOPER",
                    datetime.now(UTC) + timedelta(days=60), "Asia/Seoul", "", "ACTIVE"))
                if user == first:
                    own = goal.goal_id
                else:
                    foreign = goal.goal_id
            retriever = PersonalRetriever(PostgresProductQueries(database), PostgresM5Queries(database),
                PersistentPreferencesService(store, PostgresRoutePreferencesQuery(database)), SkillCatalog(lambda: ()))
            context = await retriever.retrieve(first, own, "내 프로필")
            serialized = context.model_dump_json()
            assert "own-profile-marker" in serialized
            assert "foreign-profile-marker" not in serialized
            assert str(foreign) not in serialized
            assert context.goal_id == own
            assert "NO_ANALYSIS" in context.warnings and "NO_ROADMAP" in context.warnings
            with pytest.raises(MissingRecordError):
                await retriever.retrieve(first, foreign, "")
        finally:
            await database.dispose()
    anyio.run(run)
