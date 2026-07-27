-- Milestone 3A: idempotent lead persistence with a configured duplicate window.
-- Run this manually in the Supabase SQL Editor after 001_init_schema.sql.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE leads ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS deduplication_bucket DATE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'website';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS raw_message TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS customer_name TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS requested_service TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS urgency TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score SMALLINT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ai_summary TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_status TEXT DEFAULT 'pending';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

UPDATE leads
SET idempotency_key = 'legacy:' || id::text
WHERE idempotency_key IS NULL;

UPDATE leads
SET deduplication_bucket = COALESCE(created_at, NOW())::date
WHERE deduplication_bucket IS NULL;

UPDATE leads
SET source = 'website'
WHERE source IS NULL;

UPDATE leads
SET raw_message = 'Legacy lead migrated before raw_message capture.'
WHERE raw_message IS NULL;

UPDATE leads
SET classification_status = 'pending'
WHERE classification_status IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'leads'
          AND column_name = 'email'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE leads ALTER COLUMN email DROP NOT NULL;
    END IF;
END $$;

ALTER TABLE leads ALTER COLUMN idempotency_key SET NOT NULL;
ALTER TABLE leads ALTER COLUMN deduplication_bucket SET NOT NULL;
ALTER TABLE leads ALTER COLUMN source SET DEFAULT 'website';
ALTER TABLE leads ALTER COLUMN source SET NOT NULL;
ALTER TABLE leads ALTER COLUMN raw_message SET NOT NULL;
ALTER TABLE leads ALTER COLUMN classification_status SET DEFAULT 'pending';
ALTER TABLE leads ALTER COLUMN classification_status SET NOT NULL;
ALTER TABLE leads ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE leads ALTER COLUMN created_at SET NOT NULL;

ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_idempotency_key_key;
DROP INDEX IF EXISTS idx_leads_idempotency_key_unique;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_raw_message_length_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_raw_message_length_check CHECK (
            char_length(btrim(raw_message)) >= 10
            AND char_length(raw_message) <= 5000
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_urgency_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_urgency_check CHECK (
            urgency IS NULL OR urgency IN ('hot', 'warm', 'cold')
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_lead_score_range_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_lead_score_range_check CHECK (
            lead_score IS NULL OR (lead_score >= 0 AND lead_score <= 100)
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_classification_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_classification_status_check CHECK (
            classification_status IN ('pending', 'classified', 'failed')
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_leads_idempotency_key ON leads(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_idempotency_bucket_unique
    ON leads(idempotency_key, deduplication_bucket);
CREATE INDEX IF NOT EXISTS idx_leads_deduplication_bucket ON leads(deduplication_bucket);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_classification_status ON leads(classification_status);
CREATE INDEX IF NOT EXISTS idx_leads_urgency ON leads(urgency);
CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
