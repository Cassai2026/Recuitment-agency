-- =============================================================================
-- Module 3 — The Zero-Lag Matcher
-- PostGIS schema additions for geo-spatial candidate matching
-- =============================================================================
-- Run this migration ONCE against the live database after schema.sql has been
-- applied.  Safe to re-run (all statements are idempotent).
-- =============================================================================

-- Enable PostGIS extension (requires PostgreSQL with PostGIS installed)
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- ---------------------------------------------------------------------------
-- Add spatial columns to candidates
-- ---------------------------------------------------------------------------
-- location  : WGS-84 point representing the candidate's home / base location
-- compliance_status : denormalised GREEN / AMBER / RED flag for fast filtering
--   GREEN  = all required certs valid, right to work confirmed
--   AMBER  = cert expiring within 30 days
--   RED    = expired, missing, or flagged
ALTER TABLE candidates
    ADD COLUMN IF NOT EXISTS location          geometry(Point, 4326),
    ADD COLUMN IF NOT EXISTS compliance_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (compliance_status IN ('GREEN', 'AMBER', 'RED', 'PENDING'));

-- ---------------------------------------------------------------------------
-- Add spatial column to jobs
-- ---------------------------------------------------------------------------
-- job_location : WGS-84 point of the job / work-site
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS job_location geometry(Point, 4326);

-- ---------------------------------------------------------------------------
-- Spatial indexes (GIST) — critical for ST_DWithin performance
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_candidates_location
    ON candidates USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_jobs_job_location
    ON jobs USING GIST (job_location);

-- Supporting indexes for the matcher's WHERE clause filters
CREATE INDEX IF NOT EXISTS idx_candidates_compliance_status
    ON candidates (compliance_status);

-- ---------------------------------------------------------------------------
-- Compliance status auto-maintenance function
-- Keeps compliance_status in sync whenever cscs/nrswa expiry dates change
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_refresh_compliance_status()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_cscs_ok  BOOLEAN;
    v_nrswa_ok BOOLEAN;
    v_expiring BOOLEAN;
    v_new_status TEXT;
BEGIN
    -- CSCS check: status must be 'valid' and expiry must be in the future
    v_cscs_ok := (
        NEW.cscs_status = 'valid'
        AND NEW.cscs_expiry_date IS NOT NULL
        AND NEW.cscs_expiry_date >= CURRENT_DATE
    );

    -- NRSWA check: only enforced when nrswa_cert_number is present
    v_nrswa_ok := (
        NEW.nrswa_cert_number IS NULL          -- no NRSWA cert required
        OR (
            NEW.nrswa_status = 'valid'
            AND NEW.nrswa_expiry_date IS NOT NULL
            AND NEW.nrswa_expiry_date >= CURRENT_DATE
        )
    );

    -- AMBER flag: any cert expiring within the next 30 days
    v_expiring := (
        (NEW.cscs_expiry_date IS NOT NULL
            AND NEW.cscs_expiry_date < CURRENT_DATE + INTERVAL '30 days'
            AND NEW.cscs_expiry_date >= CURRENT_DATE)
        OR (NEW.nrswa_cert_number IS NOT NULL
            AND NEW.nrswa_expiry_date IS NOT NULL
            AND NEW.nrswa_expiry_date < CURRENT_DATE + INTERVAL '30 days'
            AND NEW.nrswa_expiry_date >= CURRENT_DATE)
    );

    -- Derive status
    IF NOT v_cscs_ok OR NOT v_nrswa_ok THEN
        v_new_status := 'RED';
    ELSIF v_expiring THEN
        v_new_status := 'AMBER';
    ELSIF NEW.right_to_work = FALSE OR NEW.dbs_check_passed = FALSE THEN
        v_new_status := 'AMBER';
    ELSE
        v_new_status := 'GREEN';
    END IF;

    NEW.compliance_status := v_new_status;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_refresh_compliance_status
    BEFORE INSERT OR UPDATE
    ON candidates
    FOR EACH ROW
    EXECUTE FUNCTION fn_refresh_compliance_status();
