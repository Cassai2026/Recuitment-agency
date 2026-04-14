-- =============================================================================
-- Dual-Business Hub: Production-Ready PostgreSQL Schema
-- Lead Systems Architect: Paul Cassidy / Lilieth Orchestrator
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enable PostGIS for geo-spatial candidate matching (Module 3 — Zero-Lag Matcher)
CREATE EXTENSION IF NOT EXISTS postgis;

-- =============================================================================
-- CORE LAYER: Shared enums and audit infrastructure
-- =============================================================================

CREATE TYPE audit_action AS ENUM ('INSERT', 'UPDATE', 'DELETE');

-- Audit log table — captures every transaction across all modules
CREATE TABLE audit_logs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name    TEXT        NOT NULL,
    record_id     UUID        NOT NULL,
    action        audit_action NOT NULL,
    changed_by    TEXT        NOT NULL DEFAULT current_user,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    old_data      JSONB,
    new_data      JSONB
);

CREATE INDEX idx_audit_logs_table_record ON audit_logs (table_name, record_id);
CREATE INDEX idx_audit_logs_changed_at   ON audit_logs (changed_at);

-- Generic audit trigger function reused by every table
CREATE OR REPLACE FUNCTION fn_audit_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO audit_logs (table_name, record_id, action, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, 'INSERT', to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.id, 'UPDATE', to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data)
        VALUES (TG_TABLE_NAME, OLD.id, 'DELETE', to_jsonb(OLD));
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$;

-- =============================================================================
-- MODULE 1: RECRUITMENT
-- =============================================================================

CREATE TYPE ticket_status   AS ENUM ('valid', 'expired', 'pending_renewal');
CREATE TYPE placement_status AS ENUM ('active', 'completed', 'cancelled', 'on_hold');
CREATE TYPE motorway_zone   AS ENUM ('zone_a', 'zone_b', 'zone_c', 'smart_motorway', 'a_road', 'b_road', 'urban');

-- Candidates ----------------------------------------------------------------
CREATE TABLE candidates (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name          TEXT        NOT NULL,
    last_name           TEXT        NOT NULL,
    email               TEXT        UNIQUE NOT NULL,
    phone               TEXT,
    -- CSCS ticket details
    cscs_card_number    TEXT,
    cscs_card_type      TEXT,
    cscs_expiry_date    DATE,
    cscs_status         ticket_status NOT NULL DEFAULT 'pending_renewal',
    -- NRSWA (Streetworks) ticket details
    nrswa_cert_number   TEXT,
    nrswa_units         TEXT[],          -- e.g. ARRAY['Unit 1','Unit 2','Unit 10']
    nrswa_expiry_date   DATE,
    nrswa_status        ticket_status NOT NULL DEFAULT 'pending_renewal',
    -- Operational flags
    night_shift_ready   BOOLEAN     NOT NULL DEFAULT FALSE,
    right_to_work       BOOLEAN     NOT NULL DEFAULT FALSE,
    dbs_check_passed    BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Geo-spatial location (WGS-84) — used by Module 3 Zero-Lag Matcher
    location            geometry(Point, 4326),
    -- Denormalised compliance flag (GREEN/AMBER/RED/PENDING) auto-maintained by trigger
    compliance_status   TEXT        NOT NULL DEFAULT 'PENDING'
        CONSTRAINT chk_compliance_status CHECK (compliance_status IN ('GREEN', 'AMBER', 'RED', 'PENDING')),
    -- Metadata
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_candidates_email        ON candidates (email);
CREATE INDEX idx_candidates_cscs_status  ON candidates (cscs_status);
CREATE INDEX idx_candidates_nrswa_status ON candidates (nrswa_status);
CREATE INDEX idx_candidates_night_shift  ON candidates (night_shift_ready);
CREATE INDEX idx_candidates_compliance   ON candidates (compliance_status);
CREATE INDEX idx_candidates_location     ON candidates USING GIST (location);

CREATE TRIGGER trg_audit_candidates
    AFTER INSERT OR UPDATE OR DELETE ON candidates
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- Jobs (Recruitment) --------------------------------------------------------
CREATE TABLE jobs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT        NOT NULL,
    description     TEXT,
    client_name     TEXT        NOT NULL,
    location        TEXT        NOT NULL,
    motorway_zone   motorway_zone NOT NULL DEFAULT 'urban',
    -- Geo-spatial job site point (WGS-84) — used by Module 3 Zero-Lag Matcher
    job_location    geometry(Point, 4326),
    -- Scheduling
    start_date      DATE,
    end_date        DATE,
    night_shift     BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Requirements
    cscs_required   BOOLEAN     NOT NULL DEFAULT TRUE,
    nrswa_required  BOOLEAN     NOT NULL DEFAULT FALSE,
    headcount       INTEGER     NOT NULL DEFAULT 1 CHECK (headcount > 0),
    -- Pay
    pay_rate        NUMERIC(10,2),
    -- Status
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_jobs_motorway_zone ON jobs (motorway_zone);
CREATE INDEX idx_jobs_is_active     ON jobs (is_active);
CREATE INDEX idx_jobs_start_date    ON jobs (start_date);
CREATE INDEX idx_jobs_job_location  ON jobs USING GIST (job_location);

CREATE TRIGGER trg_audit_jobs
    AFTER INSERT OR UPDATE OR DELETE ON jobs
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- Placements ----------------------------------------------------------------
CREATE TABLE placements (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id    UUID            NOT NULL REFERENCES candidates (id) ON DELETE RESTRICT,
    job_id          UUID            NOT NULL REFERENCES jobs (id) ON DELETE RESTRICT,
    status          placement_status NOT NULL DEFAULT 'active',
    start_date      DATE            NOT NULL,
    end_date        DATE,
    agreed_rate     NUMERIC(10,2),
    night_shift     BOOLEAN         NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_placements_candidate ON placements (candidate_id);
CREATE INDEX idx_placements_job       ON placements (job_id);
CREATE INDEX idx_placements_status    ON placements (status);

CREATE TRIGGER trg_audit_placements
    AFTER INSERT OR UPDATE OR DELETE ON placements
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- =============================================================================
-- MODULE 2: PROPERTY PRESSURE
-- =============================================================================

CREATE TYPE surface_type    AS ENUM ('concrete', 'block_paving', 'tarmac', 'decking', 'render', 'brick', 'stone', 'roof_tile', 'other');
CREATE TYPE lead_status     AS ENUM ('new', 'contacted', 'quoted', 'won', 'lost', 'nurture');
CREATE TYPE contract_status AS ENUM ('draft', 'active', 'completed', 'cancelled', 'renewed');

-- Leads ---------------------------------------------------------------------
CREATE TABLE leads (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT        NOT NULL,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    postcode        TEXT,
    source          TEXT,        -- e.g. 'website', 'referral', 'checkatrade'
    status          lead_status NOT NULL DEFAULT 'new',
    estimated_value NUMERIC(10,2),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_leads_status    ON leads (status);
CREATE INDEX idx_leads_postcode  ON leads (postcode);

CREATE TRIGGER trg_audit_leads
    AFTER INSERT OR UPDATE OR DELETE ON leads
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- Residential Jobs ----------------------------------------------------------
CREATE TABLE residential_jobs (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID            REFERENCES leads (id) ON DELETE SET NULL,
    client_name     TEXT            NOT NULL,
    address         TEXT            NOT NULL,
    postcode        TEXT,
    -- Surface specification
    sq_meters       NUMERIC(10,2)   NOT NULL CHECK (sq_meters > 0),
    surface_type    surface_type    NOT NULL DEFAULT 'concrete',
    floors          SMALLINT        NOT NULL DEFAULT 1 CHECK (floors > 0),
    -- Logistics
    access_notes    TEXT,
    requires_scaffold BOOLEAN       NOT NULL DEFAULT FALSE,
    requires_traffic_management BOOLEAN NOT NULL DEFAULT FALSE,
    -- Commercial
    quoted_price    NUMERIC(10,2),
    final_price     NUMERIC(10,2),
    -- Scheduling
    scheduled_date  DATE,
    completed_date  DATE,
    -- Status
    is_completed    BOOLEAN         NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_residential_jobs_lead_id ON residential_jobs (lead_id);
CREATE INDEX idx_residential_jobs_postcode ON residential_jobs (postcode);
CREATE INDEX idx_residential_jobs_surface ON residential_jobs (surface_type);

CREATE TRIGGER trg_audit_residential_jobs
    AFTER INSERT OR UPDATE OR DELETE ON residential_jobs
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- Commercial Contracts ------------------------------------------------------
CREATE TABLE commercial_contracts (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    client_company      TEXT            NOT NULL,
    contact_name        TEXT,
    contact_email       TEXT,
    contact_phone       TEXT,
    site_address        TEXT            NOT NULL,
    postcode            TEXT,
    -- Contract details
    contract_ref        TEXT            UNIQUE,
    status              contract_status NOT NULL DEFAULT 'draft',
    total_sq_meters     NUMERIC(12,2)   CHECK (total_sq_meters IS NULL OR total_sq_meters > 0),
    surface_types       surface_type[],  -- multiple surface types on a commercial site
    -- Financial
    annual_value        NUMERIC(12,2),
    payment_terms       TEXT,
    -- Duration
    contract_start      DATE,
    contract_end        DATE,
    renewal_date        DATE,
    -- Operational
    visit_frequency     TEXT,            -- e.g. 'monthly', 'quarterly'
    requires_coshh      BOOLEAN         NOT NULL DEFAULT FALSE,
    requires_hot_works_permit BOOLEAN   NOT NULL DEFAULT FALSE,
    notes               TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_commercial_contracts_status  ON commercial_contracts (status);
CREATE INDEX idx_commercial_contracts_postcode ON commercial_contracts (postcode);
CREATE INDEX idx_commercial_contracts_renewal ON commercial_contracts (renewal_date);

CREATE TRIGGER trg_audit_commercial_contracts
    AFTER INSERT OR UPDATE OR DELETE ON commercial_contracts
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- =============================================================================
-- CORE LAYER: RAMS VAULT
-- Stores site-specific Risk Assessments & Method Statements
-- linked to every job type across both business modules
-- =============================================================================

CREATE TYPE rams_module     AS ENUM ('recruitment', 'property_pressure');
CREATE TYPE rams_risk_level AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TABLE rams_vault (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Polymorphic reference to the job record in either module
    module              rams_module     NOT NULL,
    job_id              UUID            NOT NULL,   -- FK enforced at app level (cross-module)
    -- Document identity
    rams_ref            TEXT            UNIQUE NOT NULL,  -- e.g. 'RAMS-2026-001'
    title               TEXT            NOT NULL,
    version             TEXT            NOT NULL DEFAULT '1.0',
    -- Assessment
    risk_level          rams_risk_level NOT NULL DEFAULT 'medium',
    hazard_summary      TEXT[],          -- list of identified hazards
    control_measures    TEXT[],          -- list of control measures
    ppe_required        TEXT[],          -- PPE list
    -- Method Statement
    method_statement    TEXT,            -- full markdown body
    -- High-pressure water hazard flags
    high_pressure_water BOOLEAN         NOT NULL DEFAULT FALSE,
    -- Working at height flags
    working_at_height   BOOLEAN         NOT NULL DEFAULT FALSE,
    max_height_meters   NUMERIC(5,2),
    scaffold_required   BOOLEAN         NOT NULL DEFAULT FALSE,
    -- Traffic management
    traffic_management  BOOLEAN         NOT NULL DEFAULT FALSE,
    motorway_zone       motorway_zone,
    night_shift_works   BOOLEAN         NOT NULL DEFAULT FALSE,
    -- Public footfall management
    public_footfall     BOOLEAN         NOT NULL DEFAULT FALSE,
    footfall_zone       TEXT,            -- e.g. 'Piccadilly', 'Covent Garden'
    -- Sign-off
    prepared_by         TEXT,
    reviewed_by         TEXT,
    approved_by         TEXT,
    issue_date          DATE,
    review_date         DATE,
    is_current          BOOLEAN         NOT NULL DEFAULT TRUE,
    -- Storage
    document_path       TEXT,            -- path or URL to the generated markdown file
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_rams_vault_module    ON rams_vault (module);
CREATE INDEX idx_rams_vault_job_id    ON rams_vault (job_id);
CREATE INDEX idx_rams_vault_ref       ON rams_vault (rams_ref);
CREATE INDEX idx_rams_vault_risk      ON rams_vault (risk_level);
CREATE INDEX idx_rams_vault_is_current ON rams_vault (is_current);

CREATE TRIGGER trg_audit_rams_vault
    AFTER INSERT OR UPDATE OR DELETE ON rams_vault
    FOR EACH ROW EXECUTE FUNCTION fn_audit_trigger();

-- =============================================================================
-- Module 3: Compliance-status auto-maintenance trigger
-- Keeps candidates.compliance_status (GREEN/AMBER/RED/PENDING) in sync
-- whenever cert expiry dates or verification flags are updated.
-- =============================================================================

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
        NEW.nrswa_cert_number IS NULL
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

CREATE TRIGGER trg_refresh_compliance_status
    BEFORE INSERT OR UPDATE
    ON candidates
    FOR EACH ROW
    EXECUTE FUNCTION fn_refresh_compliance_status();

-- =============================================================================
-- updated_at auto-maintenance trigger
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Apply updated_at trigger to all mutable tables
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'candidates', 'jobs', 'placements',
        'leads', 'residential_jobs', 'commercial_contracts',
        'rams_vault'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_updated_at_%s
             BEFORE UPDATE ON %s
             FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()',
            t, t
        );
    END LOOP;
END;
$$;
