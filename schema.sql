-- StockSafe MVP — full database schema
-- Run this entire file once, in order, in the Supabase SQL Editor.
-- Source: StockSafe PRD v2, Section 6 (tables 6.1-6.7) + Design Handoff (tables 6.8-6.11).
--
-- Row-Level Security note: Supabase enables RLS by default on tables
-- created via the SQL Editor. This app has no end-user auth — the FastAPI
-- backend is the only thing that ever talks to Supabase (using the anon key
-- server-side only, never exposed to the browser), so RLS provides no real
-- protection here and would just silently block every insert. RLS is
-- disabled per-table below instead of writing policies for a single trusted
-- server-side caller.

-- 6.1  Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- 6.2  companies
CREATE TABLE companies (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  name_clean   TEXT NOT NULL,
  ticker_nse   TEXT,
  ticker_bse   TEXT,
  cin          TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_companies_name_clean ON companies(name_clean);

-- 6.3  directors
CREATE TABLE directors (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  name_clean   TEXT NOT NULL,
  pan          TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 6.4  director_company_map
CREATE TABLE director_company_map (
  id           SERIAL PRIMARY KEY,
  director_id  INT REFERENCES directors(id),
  company_id   INT REFERENCES companies(id),
  role         TEXT,
  source       TEXT,
  UNIQUE(director_id, company_id)
);

-- 6.5  sebi_orders
-- order_number is NOT globally unique on its own: SEBI sometimes issues one
-- "omnibus" order PDF covering several unrelated companies from the same
-- systemic investigation (e.g. illiquid stock options manipulation). One
-- row is inserted per company actually named in the order, all sharing the
-- same order_number/date/violation/status — so every named company is
-- correctly searchable and scored, not just whichever one was parsed first.
CREATE TABLE sebi_orders (
  id              SERIAL PRIMARY KEY,
  order_number    TEXT NOT NULL,
  order_date      DATE NOT NULL,
  order_type      TEXT NOT NULL,
  status          TEXT NOT NULL,
  violation_type  TEXT NOT NULL,
  entity_type     TEXT NOT NULL,
  company_id      INT REFERENCES companies(id),
  director_id     INT REFERENCES directors(id),
  summary         TEXT,
  pdf_url         TEXT NOT NULL,
  raw_text        TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(order_number, company_id)
);
CREATE INDEX idx_orders_company ON sebi_orders(company_id);
CREATE INDEX idx_orders_director ON sebi_orders(director_id);
CREATE INDEX idx_orders_date ON sebi_orders(order_date DESC);

-- 6.6  stock_outcomes
CREATE TABLE stock_outcomes (
  id                  SERIAL PRIMARY KEY,
  company_id          INT REFERENCES companies(id),
  signal_combo        TEXT NOT NULL,
  had_director_order  BOOLEAN DEFAULT FALSE,
  had_auditor_change  BOOLEAN DEFAULT FALSE,
  price_change_pct    NUMERIC,
  outcome_period_days INT DEFAULT 365,
  data_source         TEXT DEFAULT 'manual_seed',
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 6.7  search_cache
CREATE TABLE search_cache (
  id           SERIAL PRIMARY KEY,
  company_id   INT REFERENCES companies(id),
  risk_score   INT NOT NULL,
  verdict      TEXT NOT NULL,
  red_flags    JSONB NOT NULL,
  pattern_stat TEXT,
  tip_of_day   TEXT,
  cached_at    TIMESTAMPTZ DEFAULT NOW(),
  expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);
CREATE INDEX idx_cache_company ON search_cache(company_id);
CREATE INDEX idx_cache_expires ON search_cache(expires_at);

-- 6.8  reminders — NEW in v2
CREATE TABLE reminders (
  id             SERIAL PRIMARY KEY,
  company_id     INT REFERENCES companies(id),
  verdict        TEXT NOT NULL,
  risk_score     INT NOT NULL,
  planned_amount NUMERIC,
  fire_at        TIMESTAMPTZ NOT NULL,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  cancelled_at   TIMESTAMPTZ
);

-- 6.9  investment_log — NEW in v2
CREATE TABLE investment_log (
  id                SERIAL PRIMARY KEY,
  company_id        INT REFERENCES companies(id),
  verdict           TEXT NOT NULL,
  risk_score        INT NOT NULL,
  amount_invested   NUMERIC NOT NULL,
  original_planned  NUMERIC NOT NULL,
  investment_type   TEXT NOT NULL,   -- "adjusted" | "full"
  check_in_date     DATE NOT NULL,   -- logged_at + 30 days
  outcome_pct       NUMERIC,         -- filled at 30-day check-in
  logged_at         TIMESTAMPTZ DEFAULT NOW()
);

-- 6.10  watchlist — NEW in v2
CREATE TABLE watchlist (
  id          SERIAL PRIMARY KEY,
  company_id  INT REFERENCES companies(id),
  log_id      INT REFERENCES investment_log(id),
  added_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 6.11  company_requests — NEW in v2
CREATE TABLE company_requests (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  requested_at TIMESTAMPTZ DEFAULT NOW()
);

-- Disable RLS on every table — see note at top of file.
ALTER TABLE companies             DISABLE ROW LEVEL SECURITY;
ALTER TABLE directors             DISABLE ROW LEVEL SECURITY;
ALTER TABLE director_company_map  DISABLE ROW LEVEL SECURITY;
ALTER TABLE sebi_orders           DISABLE ROW LEVEL SECURITY;
ALTER TABLE stock_outcomes        DISABLE ROW LEVEL SECURITY;
ALTER TABLE search_cache          DISABLE ROW LEVEL SECURITY;
ALTER TABLE reminders             DISABLE ROW LEVEL SECURITY;
ALTER TABLE investment_log        DISABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist             DISABLE ROW LEVEL SECURITY;
ALTER TABLE company_requests      DISABLE ROW LEVEL SECURITY;
