-- Reconcile the CRM retry-claim invariant with migration 007.
-- Run this manually in the Supabase SQL Editor after 007_crm_retry_claiming.sql.
--
-- Existing rows must not have an integration claim while integration_status is
-- anything other than 'failed'. The preflight query should return zero rows
-- before this migration is applied:
--
-- SELECT id
-- FROM public.leads
-- WHERE integration_claimed_at IS NOT NULL
--   AND integration_status <> 'failed';

BEGIN;

ALTER TABLE leads
    DROP CONSTRAINT IF EXISTS leads_integration_claim_status_check;

ALTER TABLE leads
    ADD CONSTRAINT leads_integration_claim_status_check CHECK (
        integration_claimed_at IS NULL
        OR integration_status = 'failed'
    );

COMMIT;
