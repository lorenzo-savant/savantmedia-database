-- ════════════════════════════════════════════════════════════════════
-- Phase 4 — Row Level Security activation
-- ────────────────────────────────────────────────────────────────────
-- Implements Fase 4 of docs/ARCHITECTURE.md:
--   "Auth + RLS för kollegor read-only."
--
-- WHY it’s safe to apply NOW (even before any auth UI exists):
--   • ENABLE RLS without a matching policy = the table is LOCKED DOWN
--     for the `anon` and `authenticated` roles. They will see zero
--     rows / get a 401-like response from PostgREST.
--   • `service_role` BYPASSES RLS entirely (postgres-level BYPASSRLS).
--     All backend code in `backend/` and server-side admin/migrate
--     routes use the SECRET service role key, so they keep working
--     unchanged.
--   • Today the frontend already calls Supabase from server-side
--     routes (Next.js App Router server components / route handlers)
--     using the service-role key. There is no client-side `anon` read
--     happening yet, so enabling RLS now has zero behavioral impact on
--     the live app. It just closes the barn door before the horse.
--
-- WHAT we add policies for:
--   • companies   → authenticated read of non-archived rows
--   • contacts    → authenticated read, gated by parent company active
--   • sources     → authenticated read, gated by parent company active
--
-- WHAT we DON’T add policies for (intentional):
--   • plans, scrape_jobs, knowledge_chunks → orchestrator-internal.
--     Only the backend (service_role) touches them. authenticated
--     colleagues should NOT see plan drafts, raw scrape errors, or
--     embedding chunks. No policy = no access for non-service roles.
--   • No INSERT/UPDATE/DELETE policies for `authenticated`. Writes
--     remain Lorenzo-only via service_role from backend/migrate
--     routes. When/if we add a colleague write UI, add explicit
--     write policies then.
-- ════════════════════════════════════════════════════════════════════


-- ── 1. Enable RLS on all 6 tables ───────────────────────────────────
alter table public.companies        enable row level security;
alter table public.contacts         enable row level security;
alter table public.sources          enable row level security;
alter table public.plans            enable row level security;
alter table public.scrape_jobs      enable row level security;
alter table public.knowledge_chunks enable row level security;


-- ── 2. Read policies for `authenticated` (colleagues) ───────────────

-- companies: only non-archived rows are visible to colleagues
create policy viewer_read_active_companies
  on public.companies
  for select
  to authenticated
  using (not arkiverad);

-- contacts: visible only if their parent company is active (non-archived)
create policy viewer_read_contacts
  on public.contacts
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.companies c
      where c.id = contacts.company_id
        and not c.arkiverad
    )
  );

-- sources: visible only if their parent company is active
-- (sources can also reference contact_id only; in that case we still
-- gate via the linked contact’s company.)
create policy viewer_read_sources
  on public.sources
  for select
  to authenticated
  using (
    (
      sources.company_id is not null
      and exists (
        select 1
        from public.companies c
        where c.id = sources.company_id
          and not c.arkiverad
      )
    )
    or
    (
      sources.contact_id is not null
      and exists (
        select 1
        from public.contacts k
        join public.companies c on c.id = k.company_id
        where k.id = sources.contact_id
          and not c.arkiverad
      )
    )
  );


-- ════════════════════════════════════════════════════════════════════
-- Reminder: service_role bypasses RLS. No explicit policy needed for
-- backend writes. plans / scrape_jobs / knowledge_chunks remain
-- service_role-only by virtue of having RLS enabled and no policy.
-- ════════════════════════════════════════════════════════════════════
