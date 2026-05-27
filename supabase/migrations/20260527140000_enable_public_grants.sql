-- ════════════════════════════════════════════════════════════════════
-- Public schema → Data API grants (PostgREST / GraphQL / supabase-js)
-- ────────────────────────────────────────────────────────────────────
-- WHY: Supabase changed the default exposure of the `public` schema.
--   • New projects created after 2026-05-30 do NOT auto-expose `public`
--     tables to the Data API.
--   • Existing projects (like ours, `tttcbwquiexahcmcuech`) keep their
--     current grants on existing tables, but tables created after
--     2026-10-30 will NOT be auto-granted to anon/authenticated either.
--
-- This migration makes our grant posture EXPLICIT so:
--   1. All 6 existing tables are unambiguously exposed.
--   2. Any future table created in `public` inherits the same grants
--      via ALTER DEFAULT PRIVILEGES — no surprise “table not found”
--      from supabase-js after the deadline.
--
-- Note: this is JUST grants. Row Level Security is added in the next
-- migration (20260527150000_enable_rls_phase4.sql) — RLS gates _what_
-- rows are visible; grants gate whether the table is reachable at all
-- through PostgREST.
--
-- Docs:
--   https://supabase.com/docs/guides/api/securing-your-api
--   https://github.com/orgs/supabase/discussions/29553  (default grant change)
-- ════════════════════════════════════════════════════════════════════


-- ── 1. Explicit grants on the 6 existing tables ─────────────────────
grant select, insert, update, delete on public.companies
  to anon, authenticated, service_role;

grant select, insert, update, delete on public.contacts
  to anon, authenticated, service_role;

grant select, insert, update, delete on public.sources
  to anon, authenticated, service_role;

grant select, insert, update, delete on public.plans
  to anon, authenticated, service_role;

grant select, insert, update, delete on public.scrape_jobs
  to anon, authenticated, service_role;

grant select, insert, update, delete on public.knowledge_chunks
  to anon, authenticated, service_role;


-- ── 2. Sequence usage (needed for default uuid/serial generation
--      via the Data API, even though we use gen_random_uuid()
--      everywhere now — keeps us safe if a future table adds a
--      bigserial column). ──────────────────────────────────────────
grant usage on all sequences in schema public
  to anon, authenticated, service_role;


-- ── 3. Default privileges for FUTURE tables/sequences ───────────────
-- So we don’t have to remember to add grants for every new table.
alter default privileges in schema public
  grant select, insert, update, delete on tables
  to anon, authenticated, service_role;

alter default privileges in schema public
  grant usage, select on sequences
  to anon, authenticated, service_role;


-- ── 4. Schema usage (idempotent on existing projects) ───────────────
grant usage on schema public to anon, authenticated, service_role;
