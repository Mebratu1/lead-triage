-- Milestone 3A compatibility: relax legacy required columns not populated yet.
-- Run this manually in the Supabase SQL Editor after 002_idempotent_lead_persistence.sql.

DO $$
DECLARE
    legacy_column TEXT;
BEGIN
    FOREACH legacy_column IN ARRAY ARRAY[
        'name',
        'email',
        'first_name',
        'last_name',
        'company',
        'job_title'
    ]
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'leads'
              AND column_name = legacy_column
              AND is_nullable = 'NO'
        ) THEN
            EXECUTE format(
                'ALTER TABLE leads ALTER COLUMN %I DROP NOT NULL',
                legacy_column
            );
        END IF;
    END LOOP;
END $$;
