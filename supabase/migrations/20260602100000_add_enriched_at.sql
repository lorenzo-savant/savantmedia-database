-- Adding enriched_at to companies for cache-first with TTL
-- This column tracks when a company was last enriched/re-scraped,
-- so the cache gate (backend/enrichment/cache_gate.py) can decide
-- whether the data is still fresh or needs re-scraping.

alter table public.companies
  add column enriched_at timestamptz;

-- Index for fast "find companies that need enrichment" queries
-- Semplice index su enriched_at: covered per enriched_at IS NULL (mai arricchite).
-- La parte stale (enriched_at < now() - TTL) non può usare indice parziale
-- perché now() non è IMMUTABLE — ma il seq scan su poche righe stale è accettabile.
create index companies_enriched_at_idx
  on public.companies(enriched_at);
