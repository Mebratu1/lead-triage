-- Milestone 3C: claim and retry safety for lead classification workers.
-- Run this manually in the Supabase SQL Editor after 004_classification_tracking_columns.sql.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_claimed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_claimed_by TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS classification_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_classification_error TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS next_classification_attempt_at TIMESTAMP WITH TIME ZONE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_classification_attempt_count_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_classification_attempt_count_check CHECK (
            classification_attempt_count >= 0
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_classification_claim_pair_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_classification_claim_pair_check CHECK (
            (
                classification_claimed_at IS NULL
                AND classification_claimed_by IS NULL
            )
            OR (
                classification_claimed_at IS NOT NULL
                AND classification_claimed_by IS NOT NULL
            )
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_next_attempt_status_check'
    ) THEN
        ALTER TABLE leads ADD CONSTRAINT leads_next_attempt_status_check CHECK (
            next_classification_attempt_at IS NULL
            OR classification_status = 'pending'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_leads_classification_claimed_at
    ON leads(classification_claimed_at);

CREATE INDEX IF NOT EXISTS idx_leads_next_classification_attempt_at
    ON leads(next_classification_attempt_at);

CREATE INDEX IF NOT EXISTS idx_leads_pending_retry_available
    ON leads(created_at ASC)
    WHERE classification_status = 'pending';

CREATE OR REPLACE FUNCTION claim_pending_leads_for_classification(
    p_batch_limit INTEGER,
    p_worker_id TEXT,
    p_claim_timeout_seconds INTEGER DEFAULT 900,
    p_max_attempts INTEGER DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    raw_message TEXT,
    source VARCHAR,
    classification_status TEXT,
    created_at TIMESTAMP WITH TIME ZONE,
    classification_claimed_at TIMESTAMP WITH TIME ZONE,
    classification_claimed_by TEXT,
    classification_attempt_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_batch_limit < 1 OR p_batch_limit > 100 THEN
        RAISE EXCEPTION 'p_batch_limit must be between 1 and 100';
    END IF;

    IF p_worker_id IS NULL OR length(btrim(p_worker_id)) = 0 THEN
        RAISE EXCEPTION 'p_worker_id is required';
    END IF;

    IF p_claim_timeout_seconds < 1 THEN
        RAISE EXCEPTION 'p_claim_timeout_seconds must be at least 1';
    END IF;

    IF p_max_attempts < 1 THEN
        RAISE EXCEPTION 'p_max_attempts must be at least 1';
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT leads.id
        FROM leads
        WHERE leads.classification_status = 'pending'
          AND leads.classification_attempt_count < p_max_attempts
          AND (
              leads.next_classification_attempt_at IS NULL
              OR leads.next_classification_attempt_at <= NOW()
          )
          AND (
              leads.classification_claimed_at IS NULL
              OR leads.classification_claimed_at < (
                  NOW() - make_interval(secs => p_claim_timeout_seconds)
              )
          )
        ORDER BY leads.created_at ASC
        LIMIT p_batch_limit
        FOR UPDATE SKIP LOCKED
    ),
    claimed AS (
        UPDATE leads
        SET classification_claimed_at = NOW(),
            classification_claimed_by = p_worker_id,
            classification_attempt_count = leads.classification_attempt_count + 1
        FROM candidates
        WHERE leads.id = candidates.id
        RETURNING
            leads.id,
            leads.raw_message,
            leads.source,
            leads.classification_status,
            leads.created_at,
            leads.classification_claimed_at,
            leads.classification_claimed_by,
            leads.classification_attempt_count
    )
    SELECT
        claimed.id,
        claimed.raw_message,
        claimed.source,
        claimed.classification_status,
        claimed.created_at,
        claimed.classification_claimed_at,
        claimed.classification_claimed_by,
        claimed.classification_attempt_count
    FROM claimed;
END;
$$;

GRANT EXECUTE ON FUNCTION claim_pending_leads_for_classification(
    INTEGER,
    TEXT,
    INTEGER,
    INTEGER
) TO service_role;
