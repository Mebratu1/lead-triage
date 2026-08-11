-- Remove the unintended retry timestamp default from the production leads table.
-- Run this manually in the Supabase SQL Editor after
-- 008_enforce_integration_claim_status.sql.
--
-- The preflight query should show the current default before this migration is
-- applied. It is read-only and must return exactly one row:
--
-- SELECT column_name, column_default, is_nullable
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND table_name = 'leads'
--   AND column_name = 'integration_next_attempt_at';

BEGIN;

ALTER TABLE leads
    ALTER COLUMN integration_next_attempt_at DROP DEFAULT;

COMMIT;
