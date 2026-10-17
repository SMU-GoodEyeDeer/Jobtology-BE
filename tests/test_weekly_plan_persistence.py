from datetime import UTC, datetime
from os import environ
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import insert, update
from sqlalchemy.engine import make_url

from jobtology_be.application.weekly_plan_models import PlanOverrides
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    calculation_traces,
    goals,
    roadmaps,
    route_proposals,
    users,
)
from jobtology_be.infrastructure.persistence.weekly_plan import PostgresWeeklyPlanStore

pytest_plugins = ('test_acceptance_persistence',)
_URL = environ.get('JOBTOLOGY_ACCEPTANCE_DATABASE_URL')
pytestmark = pytest.mark.skipif(
    bool(_URL and make_url(_URL).host not in ('127.0.0.1', 'localhost', '::1')),
    reason='Weekly plan tests only migrate disposable local PostgreSQL databases',
)


async def exercise_store(database_url):
    database = Database.create(database_url)
    owner, stranger, goal, analysis, trace, proposal, roadmap = (uuid4() for _ in range(7))
    created = datetime(2026, 10, 18, 15, tzinfo=UTC)
    store = PostgresWeeklyPlanStore(database)
    try:
        # Given
        async with database.sessions.begin() as session:
            await session.execute(insert(users), [{'id': owner}, {'id': stranger}])
            await session.execute(insert(goals).values(
                id=goal, user_id=owner, goal_mode='TARGETED', occupation_id='BACKEND_DEVELOPER',
                target_by=created, timezone='Asia/Seoul', original_time_phrase='내년',
            ))
            await session.execute(insert(analyses).values(
                id=analysis, user_id=owner, goal_id=goal, profile_version=1,
                basis_type='EDITORIAL', basis_version='v1', methodology_version='v1',
                status='READY', reference_at=created, input_snapshot={}, input_hash='hash',
            ))
            await session.execute(insert(calculation_traces).values(
                id=trace, user_id=owner, kind='ROUTE', input_hash='hash', versions={}, outputs={},
            ))
            await session.execute(insert(route_proposals).values(
                id=proposal, analysis_id=analysis, user_id=owner, proposal_hash='hash',
                profile_version=1, constraints_snapshot={
                    'available_hours_per_week': 12, 'target_by': '2027-04-14T16:00:00Z',
                }, feasibility='FEASIBLE', optimization_status='OPTIMAL', steps=[], decision_trace_id=trace,
            ))
            await session.execute(insert(roadmaps).values(
                id=roadmap, user_id=owner, goal_id=goal, proposal_id=proposal,
                title='주차 플랜', profile_version=1, created_at=created,
            ))
        plan = PlanOverrides(weekly_hours=12, rest_weeks=(10, 11), order=(), hours={}, saved_at=created)
        # When
        context = await store.get_context(owner, roadmap)
        missing = await store.get(owner, roadmap)
        first = await store.upsert(owner, roadmap, plan)
        changed = await store.upsert(owner, roadmap, plan.model_copy(update={'weekly_hours': 20}))
        persisted = await store.get(owner, roadmap)
        # Then
        assert context.created_at == created
        assert context.recommended_weekly_hours == 12
        assert context.target_date.isoformat() == '2027-04-15'
        assert missing is None
        assert first.weekly_hours == 12
        assert changed == persisted
        assert changed.weekly_hours == 20
        assert changed.saved_at != created
        for other in (stranger, uuid4()):
            with pytest.raises(MissingRecordError):
                await store.get_context(other, roadmap)
            with pytest.raises(MissingRecordError):
                await store.get(other, roadmap)
            with pytest.raises(MissingRecordError):
                await store.upsert(other, roadmap, plan)
            with pytest.raises(MissingRecordError):
                await store.delete(other, roadmap)
        assert await store.get(owner, roadmap) == changed
        await store.delete(owner, roadmap)
        assert await store.get(owner, roadmap) is None
        await store.delete(owner, roadmap)
        async with database.sessions.begin() as session:
            await session.execute(update(route_proposals).where(route_proposals.c.id == proposal)
                                  .values(constraints_snapshot={}))
        fallback = await store.get_context(owner, roadmap)
        assert fallback.recommended_weekly_hours == 10
        assert fallback.target_date is None
    finally:
        await database.dispose()


def test_weekly_plan_store_persists_upserts_deletes_and_checks_owner(acceptance_database_url):
    anyio.run(exercise_store, acceptance_database_url)
