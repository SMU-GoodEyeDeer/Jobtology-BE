-- Application database owner only; adjust jobtology_app if your restricted runtime role differs.
-- Apply even when default privileges grant table DML: the REVOKEs narrow those grants.
BEGIN;
GRANT SELECT, INSERT, UPDATE ON public.invitation_codes, public.account_access
    TO jobtology_app;
REVOKE DELETE ON public.invitation_codes, public.account_access FROM jobtology_app;
GRANT SELECT, INSERT ON public.management_audit TO jobtology_app;
REVOKE UPDATE, DELETE ON public.management_audit FROM jobtology_app;
COMMIT;
