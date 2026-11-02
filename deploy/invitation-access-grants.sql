-- Application database owner only; adjust jobtology_app if your restricted runtime role differs.
-- Existing ALTER DEFAULT PRIVILEGES may already supply these table privileges.
BEGIN;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.invitation_codes, public.account_access
    TO jobtology_app;
GRANT SELECT, INSERT ON public.management_audit TO jobtology_app;
REVOKE UPDATE, DELETE ON public.management_audit FROM jobtology_app;
COMMIT;
