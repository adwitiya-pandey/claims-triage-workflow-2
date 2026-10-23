-- Copied from claims-mcp-server tag v0.1.0 (Project A)
-- SQLite schema for Meridian Health TPA claims database

CREATE TABLE plans (
  plan_id       TEXT PRIMARY KEY CHECK (plan_id GLOB 'PLN[0-9][0-9][0-9]'),
  plan_name     TEXT NOT NULL,
  annual_limit  REAL NOT NULL CHECK (annual_limit >= 0),
  deductible    REAL NOT NULL CHECK (deductible >= 0),
  copay_pct     REAL NOT NULL CHECK (copay_pct BETWEEN 0 AND 100)
);

CREATE TABLE members (
  member_id    TEXT PRIMARY KEY CHECK (member_id GLOB 'M[0-9][0-9][0-9][0-9][0-9][0-9]'),
  plan_id      TEXT NOT NULL REFERENCES plans(plan_id),
  age_band     TEXT NOT NULL CHECK (age_band IN ('0-17','18-34','35-49','50-64','65+')),
  state        TEXT NOT NULL,
  enrolled_on  TEXT NOT NULL
);

CREATE TABLE providers (
  provider_id     TEXT PRIMARY KEY CHECK (provider_id GLOB 'PRV[0-9][0-9][0-9][0-9]'),
  specialty       TEXT NOT NULL,
  state           TEXT NOT NULL,
  network_status  TEXT NOT NULL CHECK (network_status IN ('IN_NETWORK','OUT_OF_NETWORK'))
);

CREATE TABLE coverage_rules (
  plan_id           TEXT NOT NULL REFERENCES plans(plan_id),
  procedure_code    TEXT NOT NULL CHECK (procedure_code GLOB 'PX[0-9][0-9][0-9][0-9]'),
  covered           INTEGER NOT NULL CHECK (covered IN (0,1)),
  requires_preauth  INTEGER NOT NULL CHECK (requires_preauth IN (0,1)),
  max_allowed       REAL,
  PRIMARY KEY (plan_id, procedure_code)
);

CREATE TABLE claims (
  claim_id        TEXT PRIMARY KEY CHECK (claim_id GLOB 'CLM[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
  member_id       TEXT NOT NULL REFERENCES members(member_id),
  provider_id     TEXT NOT NULL REFERENCES providers(provider_id),
  procedure_code  TEXT NOT NULL CHECK (procedure_code GLOB 'PX[0-9][0-9][0-9][0-9]'),
  diagnosis_code  TEXT NOT NULL,
  service_date    TEXT NOT NULL,
  submitted_on    TEXT NOT NULL,
  billed_amount   REAL NOT NULL CHECK (billed_amount >= 0),
  allowed_amount  REAL,
  paid_amount     REAL,
  status          TEXT NOT NULL CHECK (status IN ('SUBMITTED','PENDED','APPROVED','DENIED','PAID'))
);

CREATE TABLE claim_notes (
  note_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_id     TEXT NOT NULL REFERENCES claims(claim_id),
  created_on   TEXT NOT NULL,
  author_type  TEXT NOT NULL CHECK (author_type IN ('CLAIMANT','PROVIDER','ANALYST','SYSTEM')),
  note_text    TEXT NOT NULL
);

CREATE INDEX idx_claims_member ON claims(member_id, service_date);
CREATE INDEX idx_notes_claim   ON claim_notes(claim_id);
