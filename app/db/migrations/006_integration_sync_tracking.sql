-- Milestone 5C: outbound CRM integration sync tracking.
-- Run this manually in the Supabase SQL Editor after 005_classification_claim_retry.sql.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_last_synced_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_error TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_next_attempt_at TIMESTAMP WITH TIME ZONE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_integration_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_integration_status_check CHECK (
            integration_status IN ('pending', 'synced', 'failed')
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_integration_error_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_integration_error_status_check CHECK (
            integration_error IS NULL OR integration_status = 'failed'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_integration_synced_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_integration_synced_status_check CHECK (
            integration_last_synced_at IS NULL OR integration_status = 'synced'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_integration_retry_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_integration_retry_status_check CHECK (
            integration_next_attempt_at IS NULL OR integration_status = 'failed'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_leads_integration_status
    ON leads(integration_status);

CREATE INDEX IF NOT EXISTS idx_leads_integration_next_attempt_at
    ON leads(integration_next_attempt_at);
