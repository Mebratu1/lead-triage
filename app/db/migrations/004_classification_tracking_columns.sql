-- Milestone 3B: classification tracking metadata.
-- Run this manually in the Supabase SQL Editor after 003_relax_legacy_lead_required_columns.sql.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_error TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS classified_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_model TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_classified_at_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_classified_at_status_check CHECK (
            classified_at IS NULL OR classification_status IN ('classified', 'failed')
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_classification_error_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_classification_error_status_check CHECK (
            classification_error IS NULL OR classification_status = 'failed'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_leads_pending_created_at
    ON leads(created_at ASC)
    WHERE classification_status = 'pending';

CREATE INDEX IF NOT EXISTS idx_leads_classified_at
    ON leads(classified_at DESC);
