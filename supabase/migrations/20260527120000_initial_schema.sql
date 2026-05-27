-- ════════════════════════════════════════════════════════════════════
-- Savantsdatabas — initial schema (v2)
-- ────────────────────────────────────────────────────────────────────
-- Aligned with frontend types (lib/types.ts) and design doc section 5.
-- Includes orchestrator tables (plans, scrape_jobs, sources, knowledge_chunks).
--
-- RLS is intentionally LEFT DISABLED for the MVP / dev environment.
-- Enable it in a follow-up migration once auth roles are defined.
-- ════════════════════════════════════════════════════════════════════


-- ── Extensions ──────────────────────────────────────────────────────
create extension if not exists "pgcrypto";    -- gen_random_uuid()
create extension if not exists "pg_trgm";     -- fuzzy text search
create extension if not exists "vector";      -- pgvector for embeddings


-- ── Enums ───────────────────────────────────────────────────────────
do $$ begin
  create type storlek_kategori as enum ('liten', 'medel', 'multinationell');
exception when duplicate_object then null; end $$;

do $$ begin
  create type verifieringsmetod as enum (
    'linkedin',
    'foretagswebbplats',
    'pressmeddelande',
    'serpapi',
    'manuell',
    'annan'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type scrape_job_status as enum ('pending', 'running', 'done', 'blocked', 'failed');
exception when duplicate_object then null; end $$;

do $$ begin
  create type plan_status as enum ('draft', 'approved', 'executing', 'done', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type knowledge_kind as enum ('playbook', 'snippet', 'query_log', 'lesson');
exception when duplicate_object then null; end $$;


-- ── companies ───────────────────────────────────────────────────────
create table public.companies (
  id                     uuid primary key default gen_random_uuid(),
  schema_version         smallint not null default 2,

  -- Identity
  organisationsnummer    text,
  domain                 text,
  foretagsnamn           text not null,
  bolagsnamn             text default '',

  -- Size
  antal_anstallda        integer check (antal_anstallda is null or antal_anstallda >= 0),
  storlek_kategori       storlek_kategori,
  storlek_manuell        boolean not null default false,

  -- Headquarters address
  adress_gata            text default '',
  postnummer             text default '',
  stad                   text default '',
  region                 text default '',
  land                   text default 'Sverige',

  -- General contact
  reception_telefon      text default '',
  email_info             text default '',

  -- Scrape control + notes
  sok_fler_kontakter     boolean not null default true,
  interna_anteckningar   text default '',

  -- Archive (soft delete)
  arkiverad              boolean not null default false,
  arkiverad_datum        timestamptz,
  arkiverad_av           text default '',

  -- Provenance / attribution (e.g. "CC-BY-4.0" for Bolagsverket/SCB)
  license_label          text,

  -- Timestamps
  skapad_datum           timestamptz not null default now(),
  senast_andrad          timestamptz not null default now()
);

-- Unique org.nr (skipped if NULL) — collapses multiple branches into one record
create unique index companies_orgnr_unique
  on public.companies(organisationsnummer)
  where organisationsnummer is not null;

create index companies_storlek_active_idx
  on public.companies(storlek_kategori)
  where not arkiverad;

create index companies_arkiverad_idx
  on public.companies(arkiverad);

create index companies_foretagsnamn_trgm
  on public.companies
  using gin (foretagsnamn gin_trgm_ops);

create index companies_domain_idx
  on public.companies(domain)
  where domain is not null;

create index companies_region_idx on public.companies(region);


-- ── contacts ────────────────────────────────────────────────────────
create table public.contacts (
  id                     uuid primary key default gen_random_uuid(),
  company_id             uuid not null references public.companies(id) on delete cascade,

  namn                   text not null default '',
  roll                   text default '',
  telefon                text default '',
  email                  text default '',
  linkedin_url           text default '',

  -- Verification badge
  verifierad             boolean not null default false,
  verifieringsmetod      verifieringsmetod,
  verifieringskalla      text default '',
  verifierat_av          text default '',
  verifierat_datum       timestamptz,

  -- DM classification (Beslutsfattare)
  is_dm                  boolean,

  skapad_datum           timestamptz not null default now(),
  senast_andrad          timestamptz not null default now()
);

create index contacts_company_id_idx on public.contacts(company_id);
create index contacts_verifierad_idx on public.contacts(company_id, verifierad);
create index contacts_email_lower_idx on public.contacts(lower(email)) where email <> '';


-- ── sources (audit trail per ogni campo) ────────────────────────────
create table public.sources (
  id                     uuid primary key default gen_random_uuid(),
  company_id             uuid references public.companies(id) on delete cascade,
  contact_id             uuid references public.contacts(id) on delete cascade,

  field_name             text not null,            -- e.g. "contacts.email", "companies.antal_anstallda"
  source_url             text,
  scraper_tier           smallint,                 -- 0 (open data) | 1..5 | NULL (legacy/manual)
  fetched_at             timestamptz not null default now(),
  raw_excerpt            text,                     -- verifiable evidence snippet
  license_label          text,                     -- "CC-BY-4.0" if from open data
  critic_note            text                      -- output of LangGraph CRITIC node
);

create index sources_company_idx on public.sources(company_id);
create index sources_contact_idx on public.sources(contact_id);
create index sources_field_idx on public.sources(field_name);


-- ── plans (proposals from the orchestrator agent) ───────────────────
create table public.plans (
  id                     uuid primary key default gen_random_uuid(),
  user_prompt            text not null,
  steps                  jsonb not null default '[]'::jsonb,
  approved_steps         jsonb,
  status                 plan_status not null default 'draft',
  created_by             text default '',
  created_at             timestamptz not null default now(),
  approved_at            timestamptz,
  completed_at           timestamptz
);

create index plans_status_idx on public.plans(status);
create index plans_created_at_idx on public.plans(created_at desc);


-- ── scrape_jobs ─────────────────────────────────────────────────────
create table public.scrape_jobs (
  id                     uuid primary key default gen_random_uuid(),
  plan_id                uuid references public.plans(id) on delete set null,
  company_id             uuid references public.companies(id) on delete set null,

  query                  text,
  target_domain          text,
  tier_used              smallint,
  status                 scrape_job_status not null default 'pending',
  result_count           integer default 0,
  blocked_reason         text,
  error_message          text,
  cost_estimate          numeric not null default 0,

  started_at             timestamptz,
  finished_at            timestamptz,
  created_at             timestamptz not null default now()
);

create index scrape_jobs_plan_idx on public.scrape_jobs(plan_id);
create index scrape_jobs_status_idx on public.scrape_jobs(status);
create index scrape_jobs_target_domain_idx on public.scrape_jobs(target_domain);


-- ── knowledge_chunks (RAG memory, pgvector) ─────────────────────────
create table public.knowledge_chunks (
  id                     uuid primary key default gen_random_uuid(),
  kind                   knowledge_kind not null,
  content                text not null,
  embedding              vector(768),               -- nomic-embed-text dimensions
  metadata               jsonb not null default '{}'::jsonb,
  vault_path             text,                      -- if sourced from lorenzovault
  source_url             text,
  created_at             timestamptz not null default now()
);

create index knowledge_chunks_kind_idx on public.knowledge_chunks(kind);
create index knowledge_chunks_vault_path_idx on public.knowledge_chunks(vault_path);

-- HNSW vector index for semantic search (cosine distance)
create index knowledge_chunks_embedding_hnsw
  on public.knowledge_chunks
  using hnsw (embedding vector_cosine_ops);


-- ── Triggers: auto-update senast_andrad on row update ───────────────
create or replace function public.set_senast_andrad()
returns trigger
language plpgsql
as $$
begin
  new.senast_andrad := now();
  return new;
end;
$$;

create trigger companies_set_senast_andrad
  before update on public.companies
  for each row execute function public.set_senast_andrad();

create trigger contacts_set_senast_andrad
  before update on public.contacts
  for each row execute function public.set_senast_andrad();


-- ── Helpful views ───────────────────────────────────────────────────

-- Active companies only (mirror of getActiveCompanies() in frontend)
create or replace view public.companies_active as
  select * from public.companies where not arkiverad;

-- Per-company verification stats (for sorting/filtering by quality)
create or replace view public.companies_verification_summary as
  select
    c.id                                                       as company_id,
    count(k.id)                                                as kontakter_total,
    count(k.id) filter (where k.verifierad)                    as kontakter_verifierade,
    bool_or(k.verifierad)                                      as har_verifierad_kontakt
  from public.companies c
  left join public.contacts k on k.company_id = c.id
  group by c.id;


-- ════════════════════════════════════════════════════════════════════
-- RLS — DISABLED IN MVP.
-- Follow-up migration will:
--   1. alter table ... enable row level security
--   2. create policy "viewer_read_active" for select on companies
--      using (not arkiverad) to role authenticated
--   3. create policy "service_role_all" for all using (true) to service_role
-- ════════════════════════════════════════════════════════════════════
