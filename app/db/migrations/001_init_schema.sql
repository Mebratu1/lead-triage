-- LeadTriage Database Schema
-- Run this in Supabase SQL Editor to initialize the database

-- 1. LEADS Table (Core Lead Data)
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    phone VARCHAR(20),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company VARCHAR(255),
    job_title VARCHAR(255),
    
    -- Classification Data
    lead_score SMALLINT DEFAULT 0 CHECK (lead_score >= 0 AND lead_score <= 100),
    status VARCHAR(50) NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'qualified', 'disqualified', 'processing_error')),
    classification_rationale TEXT,
    
    -- Deduplication
    original_lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    is_duplicate BOOLEAN DEFAULT false,
    
    -- Metadata
    source VARCHAR(100),
    raw_data JSONB,
    
    -- Timestamps
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    classified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_leads_email ON leads(email);
CREATE INDEX idx_leads_status ON leads(status);
CREATE INDEX idx_leads_lead_score ON leads(lead_score);
CREATE INDEX idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX idx_leads_is_duplicate ON leads(is_duplicate);

-- 2. LEAD_TAGS Table (Multi-select tags)
CREATE TABLE lead_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tag VARCHAR(50) NOT NULL CHECK (tag IN ('sales_ready', 'needs_nurture', 'spam', 'low_value', 'high_priority')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE(lead_id, tag)
);

CREATE INDEX idx_lead_tags_lead_id ON lead_tags(lead_id);
CREATE INDEX idx_lead_tags_tag ON lead_tags(tag);

-- 3. CLASSIFICATION_HISTORY Table (Audit Trail)
CREATE TABLE classification_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    previous_score SMALLINT,
    new_score SMALLINT NOT NULL,
    previous_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    changed_by VARCHAR(100) DEFAULT 'system',
    change_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_classification_history_lead_id ON classification_history(lead_id);
CREATE INDEX idx_classification_history_created_at ON classification_history(created_at DESC);

-- 4. DUPLICATE_LOG Table (Track duplicates detected)
CREATE TABLE duplicate_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    duplicate_lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    similarity_score NUMERIC(3,2) DEFAULT 1.0 CHECK (similarity_score >= 0 AND similarity_score <= 1.0),
    match_type VARCHAR(50) NOT NULL CHECK (match_type IN ('email', 'phone', 'fuzzy_name')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_duplicate_log_original ON duplicate_log(original_lead_id);
CREATE INDEX idx_duplicate_log_duplicate ON duplicate_log(duplicate_lead_id);

-- 5. API_KEYS Table (For authentication)
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    organization VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_api_keys_is_active ON api_keys(is_active);

-- 6. AUDIT_LOG Table (Track all operations)
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    user_id VARCHAR(100) DEFAULT 'system',
    details JSONB,
    status VARCHAR(50) NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'failure')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_resource ON audit_log(resource_type, resource_id);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);

-- Enable RLS (Row Level Security) for production
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- Create a function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update updated_at on leads
CREATE TRIGGER update_leads_updated_at
BEFORE UPDATE ON leads
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
