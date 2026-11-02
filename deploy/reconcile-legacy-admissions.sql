-- Application database owner only, connected to the application database.
-- First stop ALL old backend instances and block OAuth login/callback traffic.
-- Run only during the invitation-access cutover, BEFORE reopening login traffic.
-- This repairs identities created by the old backend after migration 20261102_01.
-- Existing admissions, invitation uses, expiry and revocation are never changed.
BEGIN;

INSERT INTO public.account_access (subject_hash, user_id, source)
SELECT sha256(convert_to('https://accounts.google.com', 'UTF8') ||
              decode('00', 'hex') || convert_to(identity.subject, 'UTF8')),
       identity.user_id,
       'LEGACY'
FROM public.google_identities AS identity
WHERE identity.issuer = 'https://accounts.google.com'
ON CONFLICT (subject_hash) DO NOTHING;

-- Must return 0 while the old backend remains stopped.
SELECT count(*) AS identities_without_admission
FROM public.google_identities AS identity
WHERE NOT EXISTS (
    SELECT 1 FROM public.account_access AS access
    WHERE access.subject_hash = sha256(convert_to('https://accounts.google.com', 'UTF8') ||
                                      decode('00', 'hex') || convert_to(identity.subject, 'UTF8'))
);

COMMIT;
