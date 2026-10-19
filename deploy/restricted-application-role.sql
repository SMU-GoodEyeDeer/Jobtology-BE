-- Run once as the existing jobtology administrator, connected to database jobtology.
-- Replace the password before execution. Keep this file free of real credentials.
-- Keep the existing jobtology credential exclusively for Alembic/releases/admin work.
-- Do not grant jobtology or pg_database_owner membership to jobtology_app.

BEGIN;

CREATE ROLE jobtology_app WITH LOGIN
    PASSWORD 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE jobtology TO jobtology_app;
GRANT USAGE ON SCHEMA public TO jobtology_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jobtology_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jobtology_app;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM jobtology_app;

-- Future migrations must run as jobtology for these defaults to apply.
ALTER DEFAULT PRIVILEGES FOR ROLE jobtology IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jobtology_app;
ALTER DEFAULT PRIVILEGES FOR ROLE jobtology IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO jobtology_app;

COMMIT;

-- Then set the backend's JOBTOLOGY_DATABASE_URL to:
-- postgresql+asyncpg://jobtology_app:<URL-encoded-password>@fwxdtqu77iy8ghcjk52v5woe:5432/jobtology
-- Run Alembic separately with the existing jobtology administrator credential.
-- Never put that administrator credential back into the API/worker runtime.

SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname = 'jobtology_app';
SELECT has_schema_privilege('jobtology_app', 'public', 'USAGE') AS can_use_schema,
       has_schema_privilege('jobtology_app', 'public', 'CREATE') AS can_create_objects,
       has_table_privilege('jobtology_app', 'public.auth_sessions', 'INSERT') AS can_issue_sessions,
       has_table_privilege('jobtology_app', 'public.alembic_version', 'UPDATE') AS can_change_revision;
