"""Explicit roadmap adoption retains authority and rolls back as one transaction."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from jobtology_be.application.roadmap_revisions import RoadmapRevisionApply
from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    PersistenceConflictError,
    UserCreate,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.queries import PostgresProductQueries
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.infrastructure.persistence.weekly_plan import PostgresWeeklyPlanStore

pytest_plugins = ('test_acceptance_persistence',)


def step(key, start, hours=1):
    return {'step_key': key, 'action_id': f'study:{key}', 'template_revision': 1,
            'title': f'Learn {key}', 'estimated_hours': hours,
            'planned_start_at': start.isoformat(), 'planned_end_at': (start + timedelta(hours=hours)).isoformat(),
            'outcomes': [{'requirement_key': key, 'entity_id': f'cap:{key}', 'raw_text': key}],
            'completion_criteria': [f'Publish {key}'], 'prerequisite_step_keys': [],
            'slot_allocations': [{'slot_index': index, 'capacity_week_key': 'week',
                                  'starts_at': (start + timedelta(hours=index)).isoformat(),
                                  'ends_at': (start + timedelta(hours=index + 1)).isoformat()}
                                 for index in range(hours)]}


async def seed(database):
    user, goal, old_analysis, new_analysis, old_proposal, new_proposal, roadmap, completion = (uuid4() for _ in range(8))
    store = PostgresApplicationStore(database)
    await store.create_user(UserCreate(user_id=user))
    now = datetime.now(UTC).replace(microsecond=0)
    old_steps = [step('a', now - timedelta(days=7), 2), step('b', now - timedelta(days=6), 3),
                 step('c', now - timedelta(days=5), 1)]
    new_steps = [step('b', now + timedelta(days=1), 3), step('d', now + timedelta(days=2), 1)]
    async with database.sessions.begin() as session:
        await session.execute(insert(s.goals).values(
            id=goal, user_id=user, goal_mode='TARGETED', occupation_id='BACKEND',
            target_by=now + timedelta(days=30), timezone='UTC', original_time_phrase='one month', status='ACTIVE',
        ))
        for analysis, release in ((old_analysis, 'old-release'), (new_analysis, 'new-release')):
            await session.execute(insert(s.analyses).values(
                id=analysis, user_id=user, goal_id=goal, profile_version=1, basis_type='EDITORIAL',
                basis_version='v1', release_id=release, methodology_version='v1', status='READY',
                reference_at=now, input_snapshot={'snapshot_selection': {'occupation_id': 'BACKEND'}},
                input_hash=str(analysis), results={'requirements': [
                    {'requirement_key': 'a', 'label': 'A', 'necessity': 'REQUIRED', 'status': 'SATISFIED',
                     'credit': 1.0, 'estimated_hours': 0, 'base_estimated_hours': 2,
                     'metadata': {'ncs_level': 3, 'demand_pct': 25, 'estimated_hours': 2, 'hours_basis': 'OFFICIAL'}}]},
            ))
        for proposal, analysis, steps in ((old_proposal, old_analysis, old_steps),
                                          (new_proposal, new_analysis, new_steps)):
            trace = uuid4()
            await session.execute(insert(s.calculation_traces).values(
                id=trace, user_id=user, kind='ROUTE', input_hash=str(trace), versions={}, outputs={},
            ))
            await session.execute(insert(s.route_proposals).values(
                id=proposal, user_id=user, analysis_id=analysis, profile_version=1,
                proposal_hash=str(proposal), constraints_snapshot={
                    'available_hours_per_week': 10, 'target_by': (now + timedelta(days=30)).isoformat(),
                }, feasibility='FEASIBLE', optimization_status='OPTIMAL', steps=steps, decision_trace_id=trace,
            ))
        await session.execute(update(s.profiles).where(s.profiles.c.user_id == user)
                              .values(latest_analysis_id=new_analysis))
        await session.execute(insert(s.roadmaps).values(
            id=roadmap, user_id=user, goal_id=goal, proposal_id=old_proposal, title='Original plan',
            state='ACTIVE', profile_version=1, release_id='old-release', created_at=now - timedelta(days=7),
        ))
        ids = {}
        for index, document in enumerate(old_steps):
            key, step_id = document['step_key'], uuid4()
            ids[key] = step_id
            await session.execute(insert(s.roadmap_steps).values(
                id=step_id, roadmap_id=roadmap, step_key=key, position=index, action_id=document['action_id'],
                template_revision=1, state='COMPLETED' if key == 'a' else 'IN_PROGRESS',
                planned_start=datetime.fromisoformat(document['planned_start_at']),
                planned_end=datetime.fromisoformat(document['planned_end_at']),
                outcomes=document['outcomes'], criteria=document['completion_criteria'],
            ))
        await session.execute(insert(s.user_state_events).values(
            id=completion, user_id=user, aggregate_id=ids['a'], aggregate_type='PROFILE', version=1,
            kind='STEP_COMPLETED', payload={'outcomes': old_steps[0]['outcomes']},
        ))
        await session.execute(insert(s.step_completion_inheritances).values(
            step_id=ids['a'], original_completion_event_id=completion,
        ))
    return store, user, roadmap, new_proposal, completion, ids


def command(user, comparison):
    return RoadmapRevisionApply(user, comparison.roadmap_id, comparison.proposal_id,
                                comparison.proposal_hash, comparison.roadmap_version,
                                comparison.profile_version, comparison.plan_version)


def test_revision_comparison_is_readonly_and_apply_preserves_completed_work(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, event, ids = await seed(database)
            queries = PostgresProductQueries(database)
            before = await queries.get_roadmap(user, roadmap)
            from jobtology_be.infrastructure.persistence.m5_queries import PostgresM5Queries
            dashboard = await PostgresM5Queries(database).get_dashboard(user, before.goal_id)
            assert dashboard.analysis.proposal_id == proposal
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            assert comparison.can_apply and comparison.revised_step_count == 3
            assert [item.step_id for item in comparison.preserved_completed_steps] == [ids['a']]
            assert comparison.retained_in_progress_step_ids == (ids['b'],)
            assert comparison.unretained_in_progress_step_ids == (ids['c'],)
            assert await queries.get_roadmap(user, roadmap) == before
            applied = await store.apply_roadmap_revision(command(user, comparison))
            assert applied.previous_roadmap_id == roadmap and applied.preserved_completed_count == 1
            revised = await queries.get_roadmap(user, applied.roadmap_id)
            assert revised.state == 'ACTIVE' and revised.profile_version == 1
            assert {item.action_id: item.state for item in revised.steps} == {
                'study:a': 'COMPLETED', 'study:b': 'IN_PROGRESS', 'study:d': 'TODO',
            }
            assert next(item for item in revised.steps if item.action_id == 'study:a').title == 'Learn a'
            archived = await queries.get_roadmap(user, roadmap)
            assert archived.state == 'ARCHIVED' and archived.steps == before.steps
            async with database.sessions() as session:
                inherited = await session.scalar(select(s.step_completion_inheritances.c.original_completion_event_id)
                    .join(s.roadmap_steps, s.roadmap_steps.c.id == s.step_completion_inheritances.c.step_id)
                    .where(s.roadmap_steps.c.roadmap_id == revised.roadmap_id, s.roadmap_steps.c.state == 'COMPLETED'))
                assert inherited == event
                assert await session.scalar(select(func.count()).select_from(s.user_state_events)) == 1
                assert await session.scalar(select(func.count()).select_from(s.user_capabilities)) == 0
            context = await PostgresWeeklyPlanStore(database).get_context(user, revised.roadmap_id)
            historical = next(item for item in revised.steps if item.state == 'COMPLETED')
            assert context.step_hours[historical.step_key] == 2
            assert context.step_metadata[historical.step_key].ncs_level == 3
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_failed_revision_activation_leaves_original_plan_and_progress_intact(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, _, _ = await seed(database)
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            before = await PostgresProductQueries(database).get_roadmap(user, roadmap)
            async with database.sessions.begin() as session:
                await session.execute(text("""CREATE FUNCTION reject_revision_activation() RETURNS trigger AS $$
                    BEGIN IF NEW.state = 'ACTIVE' THEN RAISE EXCEPTION 'activation refused'; END IF; RETURN NEW; END;
                    $$ LANGUAGE plpgsql"""))
                await session.execute(text("""CREATE TRIGGER reject_revision_activation BEFORE UPDATE ON roadmaps
                    FOR EACH ROW EXECUTE FUNCTION reject_revision_activation()"""))
            with pytest.raises(DBAPIError):
                await store.apply_roadmap_revision(command(user, comparison))
            assert await PostgresProductQueries(database).get_roadmap(user, roadmap) == before
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(s.roadmaps)) == 1
                assert await session.scalar(select(func.count()).select_from(s.roadmap_steps)) == 3
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_revision_cas_and_ownership_checks_do_not_create_drafts(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, _, _ = await seed(database)
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            with pytest.raises(MissingRecordError):
                await store.get_roadmap_revision(uuid4(), roadmap, proposal)
            for patch in ({'expected_profile_version': 2}, {'expected_roadmap_version': 2},
                          {'expected_plan_version': 1}, {'expected_proposal_hash': 'different'}):
                with pytest.raises(PersistenceConflictError):
                    await store.apply_roadmap_revision(replace(command(user, comparison), **patch))
            async with database.sessions.begin() as session:
                await session.execute(update(s.profiles).where(s.profiles.c.user_id == user).values(latest_analysis_id=None))
            blocked = await store.get_roadmap_revision(user, roadmap, proposal)
            assert not blocked.can_apply and 'STALE_PROPOSAL' in blocked.blocking_reasons
            with pytest.raises(PersistenceConflictError):
                await store.apply_roadmap_revision(command(user, blocked))
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(s.roadmaps)) == 1
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_concurrent_revision_adoptions_activate_exactly_one_plan(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, _, _ = await seed(database)
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            successes, conflicts = [], []
            async def apply():
                try:
                    successes.append(await store.apply_roadmap_revision(command(user, comparison)))
                except PersistenceConflictError as error:
                    conflicts.append(error)
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(apply)
                tasks.start_soon(apply)
            assert len(successes) == len(conflicts) == 1
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(s.roadmaps).where(s.roadmaps.c.state == 'ACTIVE')) == 1
                assert await session.scalar(select(func.count()).select_from(s.roadmaps)) == 2
        finally:
            await database.dispose()
    anyio.run(exercise)


@pytest.mark.parametrize('damage', ['missing_lineage', 'changed_outcomes', 'unknown_hours'])
def test_revision_explicitly_blocks_unverifiable_completed_history(acceptance_database_url, damage):
    from sqlalchemy import delete

    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, event, ids = await seed(database)
            async with database.sessions.begin() as session:
                if damage == 'missing_lineage':
                    await session.execute(delete(s.step_completion_inheritances).where(
                        s.step_completion_inheritances.c.step_id == ids['a']))
                elif damage == 'changed_outcomes':
                    await session.execute(update(s.user_state_events).where(s.user_state_events.c.id == event)
                                          .values(payload={'outcomes': []}))
                else:
                    original_proposal = await session.scalar(select(s.roadmaps.c.proposal_id).where(s.roadmaps.c.id == roadmap))
                    documents = await session.scalar(select(s.route_proposals.c.steps).where(s.route_proposals.c.id == original_proposal))
                    documents[0].pop('estimated_hours')
                    await session.execute(update(s.route_proposals).where(s.route_proposals.c.id == original_proposal).values(steps=documents))
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            assert not comparison.can_apply
            expected = 'COMPLETED_HISTORY_UNAVAILABLE' if damage == 'unknown_hours' else 'COMPLETED_HISTORY_UNVERIFIED'
            assert expected in comparison.blocking_reasons
            with pytest.raises(PersistenceConflictError):
                await store.apply_roadmap_revision(command(user, comparison))
            assert (await PostgresProductQueries(database).get_roadmap(user, roadmap)).state == 'ACTIVE'
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_revision_retains_completed_custom_schedule_and_blocks_late_archived_plan_writes(acceptance_database_url):
    from jobtology_be.application.weekly_plan import build_weekly_plan
    from jobtology_be.application.weekly_plan_models import PlanOverrides, PlanStepInput

    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, _, ids = await seed(database)
            queries, plans = PostgresProductQueries(database), PostgresWeeklyPlanStore(database)
            old = await queries.get_roadmap(user, roadmap)
            context = await plans.get_context(user, roadmap)
            inputs = tuple(PlanStepInput(replace(item, state='TODO'), None) for item in old.steps)
            custom = PlanOverrides(weekly_hours=5, rest_weeks=(), order=(), hours={str(ids['a']): 5},
                                   saved_at=context.created_at)
            preview = build_weekly_plan(context, inputs, custom)
            completed = preview.steps[0].model_copy(update={'state': 'COMPLETED'})
            saved = await plans.upsert(user, roadmap, custom.model_copy(update={
                'step_schedules': {str(ids['a']): completed},
            }))
            comparison = await store.get_roadmap_revision(user, roadmap, proposal)
            assert comparison.plan_version == saved.version == 1
            assert comparison.resets_future_customization
            assert comparison.constraint_changes['available_hours_per_week'] == (5, 10)
            applied = await store.apply_roadmap_revision(command(user, comparison))
            revised = await queries.get_roadmap(user, applied.roadmap_id)
            new_context = await plans.get_context(user, applied.roadmap_id)
            new_overrides = await plans.get(user, applied.roadmap_id)
            new_plan = build_weekly_plan(new_context, tuple(PlanStepInput(item, None) for item in revised.steps), new_overrides)
            retained = next(item for item in new_plan.steps if item.state == 'COMPLETED')
            assert (retained.hours, retained.start_date, retained.end_date, retained.week_allocations,
                    retained.planned_start_at, retained.planned_end_at) == (
                completed.hours, completed.start_date, completed.end_date, completed.week_allocations,
                completed.planned_start_at, completed.planned_end_at)
            assert await plans.get(user, roadmap) == saved
            with pytest.raises(PersistenceConflictError):
                await plans.upsert(user, roadmap, saved)
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_revision_http_contract_and_historical_skill_lookup(acceptance_database_url):
    import httpx
    from fastapi import FastAPI

    from jobtology_be.api.errors import register_error_handlers
    from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
    from jobtology_be.api.product_queries import require_product_queries
    from jobtology_be.api.roadmaps import require_roadmap_service, router
    from jobtology_be.application.services.roadmaps import PersistentRoadmapService

    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            store, user, roadmap, proposal, _, _ = await seed(database)
            app = FastAPI()
            register_error_handlers(app)
            app.include_router(router)
            async def principal():
                return AuthenticatedPrincipal(user_id=user)
            app.dependency_overrides[require_authenticated_principal] = principal
            app.dependency_overrides[require_product_queries] = lambda: PostgresProductQueries(database)
            app.dependency_overrides[require_roadmap_service] = lambda: PersistentRoadmapService(store)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                comparison = await client.get(f'/roadmaps/{roadmap}/revision', params={'proposal_id': str(proposal)})
                assert comparison.status_code == 200
                value = comparison.json()
                payload = {'proposal_id': value['proposal_id'], 'expected_proposal_hash': value['proposal_hash'],
                           'expected_profile_version': value['profile_version'],
                           'expected_roadmap_version': value['roadmap_version'], 'expected_plan_version': value['plan_version']}
                adopted = await client.post(f'/roadmaps/{roadmap}/revision', json=payload)
                assert adopted.status_code == 200, adopted.text
                assert adopted.json()['state'] == 'ACTIVE'
                assert adopted.json()['preserved_completed_count'] == 1
                new_id = adopted.json()['roadmap_id']
                stale = await client.post(f'/roadmaps/{roadmap}/revision', json=payload)
                assert stale.status_code == 409
                metadata = await client.get(f'/roadmaps/{new_id}/skills')
                assert metadata.status_code == 200, metadata.text
                saved = (await client.get(f'/roadmaps/{new_id}')).json()
                historical = next(item for item in saved['steps'] if item['state'] == 'COMPLETED')
                historical_skill = next(item for item in metadata.json()['skills'] if historical['step_id'] in item['step_ids'])
                assert historical_skill['status'] == 'SATISFIED' and historical_skill['ncs_level'] == 3
        finally:
            await database.dispose()
    anyio.run(exercise)
