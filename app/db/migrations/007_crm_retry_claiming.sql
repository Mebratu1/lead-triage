-- Milestone 5E: concurrent-safe CRM retry claiming and exponential backoff state.
-- Run this manually in the Supabase SQL Editor after 006_integration_sync_tracking.sql.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_retry_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_claimed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS integration_claimed_by TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'leads_integration_retry_attempt_count_check'
    ) THEN
        ALTER TABLE leads
        ADD CONSTRAINT leads_integration_retry_attempt_count_check CHECK (
            integration_retry_attempt_count >= 0
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'leads_integration_claim_pair_check'
    ) THEN
        ALTER TABLE leads
        ADD CONSTRAINT leads_integration_claim_pair_check CHECK (
            (
                integration_claimed_at IS NULL
                AND integration_claimed_by IS NULL
            )
            OR (
                integration_claimed_at IS NOT NULL
                AND integration_claimed_by IS NOT NULL
            )
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'leads_integration_claim_status_check'
    ) THEN
        ALTER TABLE leads
        ADD CONSTRAINT leads_integration_claim_status_check CHECK (
            integration_claimed_at IS NULL
            OR integration_status = 'failed'
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_leads_integration_retry_due
    ON leads(integration_next_attempt_at ASC)
    WHERE integration_status = 'failed'
      AND integration_next_attempt_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_leads_integration_claimed_at
    ON leads(integration_claimed_at);

CREATE OR REPLACE FUNCTION claim_due_leads_for_crm_sync(
    p_batch_limit INTEGER,
    p_worker_id TEXT,
    p_claim_timeout_seconds INTEGER DEFAULT 300,
    p_max_attempts INTEGER DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    source VARCHAR,
    raw_message TEXT,
    customer_name TEXT,
    email TEXT,
    phone TEXT,
    classification_status TEXT,
    urgency TEXT,
    ai_summary TEXT,
    classification_attempt_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE,
    classified_at TIMESTAMP WITH TIME ZONE,
    integration_status TEXT,
    integration_last_synced_at TIMESTAMP WITH TIME ZONE,
    integration_next_attempt_at TIMESTAMP WITH TIME ZONE,
    integration_retry_attempt_count INTEGER,
    integration_claimed_at TIMESTAMP WITH TIME ZONE,
    integration_claimed_by TEXT
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

    -- A process can die after claiming its last permitted retry. Move stale
    -- exhausted rows out of the due queue before selecting new work.
    UPDATE leads
    SET integration_next_attempt_at = NULL,
        integration_error = 'crm_retry_exhausted',
        integration_claimed_at = NULL,
        integration_claimed_by = NULL
    WHERE leads.integration_status = 'failed'
      AND leads.integration_next_attempt_at IS NOT NULL
      AND leads.integration_next_attempt_at <= NOW()
      AND leads.integration_retry_attempt_count >= p_max_attempts
      AND (
          leads.integration_claimed_at IS NULL
          OR leads.integration_claimed_at < (
              NOW() - make_interval(secs => p_claim_timeout_seconds)
          )
      );

    RETURN QUERY
    WITH candidates AS (
        SELECT leads.id
        FROM leads
        WHERE leads.classification_status = 'classified'
          AND leads.integration_status = 'failed'
          AND leads.integration_next_attempt_at IS NOT NULL
          AND leads.integration_next_attempt_at <= NOW()
          AND leads.integration_retry_attempt_count < p_max_attempts
          AND (
              leads.integration_claimed_at IS NULL
              OR leads.integration_claimed_at < (
                  NOW() - make_interval(secs => p_claim_timeout_seconds)
              )
          )
        ORDER BY leads.integration_next_attempt_at ASC
        LIMIT p_batch_limit
        FOR UPDATE SKIP LOCKED
    ),
    claimed AS (
        UPDATE leads
        SET integration_claimed_at = NOW(),
            integration_claimed_by = p_worker_id,
            integration_retry_attempt_count =
                leads.integration_retry_attempt_count + 1
        FROM candidates
        WHERE leads.id = candidates.id
        RETURNING
            leads.id,
            leads.source,
            leads.raw_message,
            leads.customer_name,
            leads.email,
            leads.phone,
            leads.classification_status,
            leads.urgency,
            leads.ai_summary,
            leads.classification_attempt_count,
            leads.created_at,
            leads.classified_at,
            leads.integration_status,
            leads.integration_last_synced_at,
            leads.integration_next_attempt_at,
            leads.integration_retry_attempt_count,
            leads.integration_claimed_at,
            leads.integration_claimed_by
    )
    SELECT * FROM claimed;
END;
$$;

REVOKE ALL ON FUNCTION claim_due_leads_for_crm_sync(
    INTEGER,
    TEXT,
    INTEGER,
    INTEGER
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION claim_due_leads_for_crm_sync(
    INTEGER,
    TEXT,
    INTEGER,
    INTEGER
) TO service_role;
