-- TendAI BOQ engine: PostgreSQL + pgvector schema (Layer 2 master data and Layer 3 scraped BOQs).
--
-- This is the target schema for when the BOQ engine moves off the app's SQLite file. The live app uses the SQLite
-- version in tender_digest/boq/masters.py, which has the same tables and columns (minus the vector columns).
-- NOT yet run against a live PostgreSQL server: load it on a scratch database first.
--
-- The vector size must equal your embedding model's output size. 1024 is a placeholder; change it before loading.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS company (
    company_id   BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    erp_name     TEXT,                       -- e.g. 'StrategicERP'; NULL for a client with no ERP
    last_synced  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 1. Item / Material master (synced from the ERP)
CREATE TABLE IF NOT EXISTS item_master (
    company_id            BIGINT NOT NULL REFERENCES company ON DELETE CASCADE,
    erp_item_code         TEXT   NOT NULL,
    item_name             TEXT   NOT NULL,
    detailed_specification TEXT  NOT NULL DEFAULT '',
    category              TEXT   NOT NULL DEFAULT '',
    primary_uom           TEXT   NOT NULL,            -- canonical unit: CUM, SQM, RMT, MT, KG, NOS, ...
    standard_base_rate    NUMERIC(14,2),
    is_composite          BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE: the rate already includes wastage, labour and machinery
    sor_code              TEXT,                       -- DSR / SOR code this item maps to, for exact lookup
    hsn_sac_code          TEXT,
    gst_pct               NUMERIC(5,2),
    embedding             vector(1024),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, erp_item_code)
);
CREATE INDEX IF NOT EXISTS item_master_uom_idx  ON item_master (company_id, primary_uom);
CREATE INDEX IF NOT EXISTS item_master_sor_idx  ON item_master (company_id, sor_code) WHERE sor_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS item_master_vec_idx  ON item_master USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS item_master_name_idx ON item_master USING gin (item_name gin_trgm_ops);

-- 2. Activity / composite rate master: one BOQ task built from material, labour and machinery
CREATE TABLE IF NOT EXISTS activity_master (
    company_id      BIGINT NOT NULL REFERENCES company ON DELETE CASCADE,
    activity_code   TEXT   NOT NULL,
    description     TEXT   NOT NULL,
    uom             TEXT   NOT NULL,
    material_cost   NUMERIC(14,2) NOT NULL DEFAULT 0,
    labor_cost      NUMERIC(14,2) NOT NULL DEFAULT 0,
    machinery_cost  NUMERIC(14,2) NOT NULL DEFAULT 0,
    overhead_cost   NUMERIC(14,2) NOT NULL DEFAULT 0,
    wastage_pct     NUMERIC(5,2)  NOT NULL DEFAULT 0,
    erp_item_code   TEXT,                              -- the item this activity prices, if any
    embedding       vector(1024),
    PRIMARY KEY (company_id, activity_code)
);
CREATE INDEX IF NOT EXISTS activity_master_vec_idx ON activity_master USING hnsw (embedding vector_cosine_ops);

-- 3. UOM and conversion master
CREATE TABLE IF NOT EXISTS uom_master (
    alias           TEXT PRIMARY KEY,                  -- spelling with only letters and digits, lower case: 'cu.m' -> 'cum'
    canonical_uom   TEXT NOT NULL,
    dimension       TEXT NOT NULL,
    factor_to_base  NUMERIC(18,8) NOT NULL             -- 1 MT = 1000 KG, 100 SFT = 9.29 SQM
);

-- 4. Standard SOR / DSR reference master (CPWD DSR, NHAI, state PWD schedules)
CREATE TABLE IF NOT EXISTS sor_master (
    source          TEXT NOT NULL,                     -- e.g. 'CPWD DSR 2023', 'MP PWD SOR 2024'
    sor_code        TEXT NOT NULL,
    description     TEXT NOT NULL,
    uom             TEXT NOT NULL,
    rate            NUMERIC(14,2) NOT NULL,
    state           TEXT,
    embedding       vector(1024),
    PRIMARY KEY (source, sor_code)
);
CREATE INDEX IF NOT EXISTS sor_master_vec_idx ON sor_master USING hnsw (embedding vector_cosine_ops);

-- 5. Location / cost index master
CREATE TABLE IF NOT EXISTS location_index (
    state              TEXT NOT NULL,
    district           TEXT NOT NULL DEFAULT '',
    cost_index         NUMERIC(6,3) NOT NULL DEFAULT 1.000,   -- multiplier on base rates
    hill_carriage_pct  NUMERIC(5,2) NOT NULL DEFAULT 0,
    royalty_per_cum    NUMERIC(10,2),
    note               TEXT,
    PRIMARY KEY (state, district)
);

-- 6. Learned alias master (AI memory): human-verified tender wording -> ERP item
CREATE TABLE IF NOT EXISTS learned_alias (
    company_id      BIGINT NOT NULL REFERENCES company ON DELETE CASCADE,
    alias_key       TEXT   NOT NULL,                   -- normalised words of the tender description
    uom             TEXT   NOT NULL,
    erp_item_code   TEXT   NOT NULL,
    raw_description TEXT,
    verified_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, alias_key, uom)
);

-- Scraped and parsed BOQs
CREATE TABLE IF NOT EXISTS boq_document (
    boq_id        BIGSERIAL PRIMARY KEY,
    company_id    BIGINT REFERENCES company ON DELETE SET NULL,
    tender_key    TEXT NOT NULL,                       -- the tender's key in the app's tender table
    source_name   TEXT,                                -- file name or URL it came from
    rate_type     TEXT NOT NULL CHECK (rate_type IN ('item_rate', 'percentage_rate', 'unknown')),
    estimated_total NUMERIC(16,2),
    warnings      JSONB NOT NULL DEFAULT '[]',
    parsed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS boq_document_tender_idx ON boq_document (tender_key);

CREATE TABLE IF NOT EXISTS boq_line (
    boq_id          BIGINT NOT NULL REFERENCES boq_document ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    sr              TEXT,
    section         TEXT,
    description     TEXT NOT NULL,
    uom_raw         TEXT,
    uom             TEXT,                              -- canonical, NULL if not recognised
    qty             NUMERIC(16,3),
    est_rate        NUMERIC(14,2),
    est_amount      NUMERIC(16,2),
    sor_code        TEXT,
    match_status    TEXT CHECK (match_status IN ('AUTO_MATCHED', 'NEEDS_REVIEW', 'NEW_NON_SOR_ITEM')),
    match_tier      TEXT,                              -- alias, sor_code, semantic_rules, semantic_llm, none
    confidence      NUMERIC(4,3),
    erp_item_code   TEXT,
    candidates      JSONB NOT NULL DEFAULT '[]',
    reason          TEXT,
    PRIMARY KEY (boq_id, line_no)
);
