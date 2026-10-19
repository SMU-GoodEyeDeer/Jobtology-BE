-- Run only after deploying Google-only authentication with guest issuance disabled.
-- Run as the migration/admin role in the application database, never the source catalog.
-- Accounts with Google identities and all non-user LLM credentials are preserved.
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE users, google_identities, profiles, auth_sessions, user_state_events,
    user_capabilities, goals, route_preferences, analyses, calculation_traces,
    route_proposals, roadmaps, roadmap_steps, step_dependencies,
    step_completion_inheritances, recompute_requests, recompute_contexts,
    outbox_jobs, idempotency_records, roadmap_plan_overrides, account_rate_limits
    IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE legacy_guest_ids ON COMMIT DROP AS
SELECT u.id FROM users u
WHERE NOT EXISTS (SELECT 1 FROM google_identities g WHERE g.user_id = u.id);

SELECT count(*) AS guest_accounts_to_delete FROM legacy_guest_ids;

DELETE FROM step_completion_inheritances
WHERE step_id IN (SELECT s.id FROM roadmap_steps s JOIN roadmaps r ON r.id = s.roadmap_id
                  WHERE r.user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM step_dependencies
WHERE roadmap_id IN (SELECT id FROM roadmaps WHERE user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM roadmap_plan_overrides
WHERE roadmap_id IN (SELECT id FROM roadmaps WHERE user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM roadmap_steps
WHERE roadmap_id IN (SELECT id FROM roadmaps WHERE user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM roadmaps WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM outbox_jobs
WHERE recompute_request_id IN
    (SELECT id FROM recompute_requests WHERE user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM recompute_contexts
WHERE request_id IN
    (SELECT id FROM recompute_requests WHERE user_id IN (SELECT id FROM legacy_guest_ids));
DELETE FROM recompute_requests WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM route_proposals WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM calculation_traces WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM analyses WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM user_capabilities WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM route_preferences WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM idempotency_records WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM goals WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM user_state_events WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM account_rate_limits WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM profiles WHERE user_id IN (SELECT id FROM legacy_guest_ids);
DELETE FROM users WHERE id IN (SELECT id FROM legacy_guest_ids);

-- Invalidate attempts started before this authentication cutover.
DELETE FROM oauth_login_attempts;

SELECT count(*) AS remaining_guest_accounts FROM users u
WHERE NOT EXISTS (SELECT 1 FROM google_identities g WHERE g.user_id = u.id);

COMMIT;
