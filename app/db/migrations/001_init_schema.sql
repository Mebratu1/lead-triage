-- LeadTriage aligned lead schema
-- Run this manually in the Supabase SQL Editor.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    source VARCHAR(50) NOT NULL DEFAULT 'website',
    raw_message TEXT NOT NULL CHECK (
        char_length(btrim(raw_message)) >= 10
        AND char_length(raw_message) <= 5000
    ),
    customer_name TEXT,
    email TEXT,
    phone TEXT,
    requested_service TEXT,
    urgency TEXT CHECK (urgency IS NULL OR urgency IN ('hot', 'warm', 'cold')),
    lead_score SMALLINT CHECK (
        lead_score IS NULL OR (lead_score >= 0 AND lead_score <= 100)
    ),
    ai_summary TEXT,
    classification_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        classification_status IN ('pending', 'classified', 'failed')
    ),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_idempotency_key ON leads(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_classification_status ON leads(classification_status);
CREATE INDEX IF NOT EXISTS idx_leads_urgency ON leads(urgency);
CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
