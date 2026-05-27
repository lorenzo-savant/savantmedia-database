---
tags: [projects, architecture, database, scraping, rag, agents]
project: savantmedia-database
created: 2026-05-27
updated: 2026-05-27
priority: high
status: design
related:
  - "[[🕷️ Web Scraping & SERP]]"
  - "[[🕷️ Scraping Lab]]"
  - "[[🧠 AI Agents & Infrastructure]]"
  - "[[🔍 OSINT]]"
related_repos:
  - "C:/Users/loren/Desktop/dev-projects/allabolag-scrape"
---

# savantmedia-database — Architettura

> Hub centrale Savant Media per il dato aziendale: **DB condiviso ai colleghi + orchestratore RAG che pianifica e governa lo scraping**, riusando lo stack già scelto nel vault (`[[🕷️ Web Scraping & SERP]]`) e la pipeline B2B Contact Enrichment validata (548 aziende, 292 email, costo 0 kr).

---

## 1. Contesto — cosa esiste, cosa manca

### Cosa esiste (da riusare, non riscrivere)

| Asset | Dove | Stato | Cosa porta |
|---|---|---|---|
| Front Next.js 15 + React 19 + Tailwind 4 | `savantmedia-database/` (questo repo) | Solo `localStorage`, CRUD + import/export CSV/JSON | Modello `Company`/`Contact` già pensato, UI di base |
| `allabolag-scrape` | `dev-projects/allabolag-scrape/` | Funzionante ma frammentato in N script `phase*.py` | Scrapers modulari (`allabolag`, `merinfo`, `linkedin_serp`, `contact_finder`, `browser`, `serp`), pipeline `scorer`+`exporter` |
| Pipeline B2B Contact Enrichment | Doc vault `🕷️ Web Scraping & SERP#🏆` | **Validata** (53% email coverage, 2.548 contatti unici) | Metodo ripetibile: SERP+WebFetch → regole rigide di verifica → JSONL → xlsx |
| Stack ranking | `🕷️ Web Scraping & SERP` | Scelte 🔴/🟡/🟢 documentate | crawl4ai (🔴), playwright (🔴), searxng (🔴), openserp (🔴), browser-use (🔴), PageIndex (🔴) |
| Knowledge vault | `lorenzovault/` (Obsidian) | Ricco di lezioni e decisioni | Memoria semantica dell'agente RAG |
| Agenti/skill | `lorenzovault/.claude/` | 3 Discipline Agents + 18 skill | Sisyphus/Hephaestus/Prometheus + skill custom |

### Cosa manca

1. **DB condiviso reale** — `localStorage` non è multi-utente. I colleghi non possono leggere.
2. **Orchestratore unico** — l'attuale `allabolag-scrape` è una collezione di script `phase*.py` che non parlano fra loro. La "unified scrape facade" è una decisione aperta in `[[🕷️ Scraping Lab#🚦 Decisioni aperte]]`: questo progetto la materializza.
3. **Memoria persistente strutturata dell'agente** — le lezioni sono nel vault, ma vanno indicizzate e interrogabili dall'agente in modo programmatico.
4. **Loop plan→approve→execute** — oggi lanci script a mano e validi a posteriori. Manca un agente che ti propone cosa cercare, tu approvi, lui esegue.

---

## 2. Vision

Un **cockpit single-pane** dove tu scrivi:

> «Trovami i CTO delle aziende IT-konsulter in Skåne län che fatturano 5–500 Mkr»

L'agente:

1. **Recall** — cerca nel DB e nella memoria: lo sappiamo già? Per che parte?
2. **Plan** — propone una sequenza di query e fonti, ogni step con: query esatta, fonte, tier scraper, costo atteso. Tu vedi il piano in UI.
3. **Approve** — clicchi "esegui" su step singoli o sull'intero piano. Puoi anche modificare le query prima.
4. **Execute** — job queue lancia worker che eseguono con tier escalation automatico (T1 → T5).
5. **Reconcile** — LLM normalizza/dedup, scrive nel DB, registra la lezione (`per allabolag/bransch-sök Tier 2 sufficiente`, `linkedin pubblico richiede T4 con sessione`).
6. **Update memory** — playbook aggiornato nel vault o nel pgvector log.

I colleghi vedono solo il DB pulito e validato, sempre aggiornato grazie a Supabase Realtime. Lo scraping vive solo sulla macchina dev.

---

## 3. Architettura (diagramma)

```
┌──────────────────────────────────────────────────────────────────┐
│  COLLEGHI Savant Media (read consumers)                          │
│  ─────────────────────────────────────────────────────────────   │
│  Next.js 15 (questo repo) — UI ricerca, filtri, export           │
│  Deploy: Vercel free + custom domain, o LAN via Tailscale        │
│  Auth: Supabase Auth (magic link, dominio @savantmedia.se)       │
│  RLS: ruolo "viewer" read-only su tabelle canoniche              │
└──────────────────────────┬───────────────────────────────────────┘
                           │ supabase-js (REST + Realtime)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  DB CONDIVISO — Supabase free tier (500MB Postgres)              │
│  ─────────────────────────────────────────────────────────────   │
│  Schema:                                                         │
│    companies        — canoniche, normalizzate, dedup             │
│    contacts         — collegate a companies, con verifica email  │
│    sources          — provenienza per ogni campo (audit trail)   │
│    scrape_jobs      — log di ogni job: query, tier, esito        │
│    plans            — piani proposti/approvati/eseguiti          │
│    knowledge_chunks — pgvector: snippet pagine, query, lezioni   │
│  RLS: viewer read-only; dev write tramite service_role           │
│  Realtime: companies/contacts → la UI dei colleghi vive          │
└──────────────────────────┬───────────────────────────────────────┘
                           │ insert/update
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  MACCHINA DEV LORENZO — Orchestratore + Scraping                 │
│  ─────────────────────────────────────────────────────────────   │
│                                                                  │
│  Next.js (questo repo) → page /orchestrator (cockpit)            │
│       ↓ SSE / WebSocket                                          │
│  FastAPI (backend/) — endpoint plan/approve/execute/status       │
│       ↓                                                          │
│  LangGraph agente RAG                                            │
│    nodes: recall → plan → wait_approval → execute → reconcile    │
│    LLM "thinking":  Groq Llama 3.3 70B free (decisioni)          │
│    LLM "bulk":      Ollama Llama 3.1 8B locale (normalizzazione) │
│    Memory backends:                                              │
│      - Postgres canonical (DB sopra)                             │
│      - pgvector (chunks indicizzati)                             │
│      - Vault Obsidian (lettura playbook + scrittura lezioni .md) │
│      - gbrain MCP (knowledge graph progetti/clienti)             │
│       ↓                                                          │
│  Job queue: Redis locale (docker) + RQ Python                    │
│       ↓                                                          │
│  Scraper workers — tier escalation                               │
│       T1: searxng self-host (+ openserp fallback)                │
│       T2: httpx + BeautifulSoup + trafilatura                    │
│       T3: crawl4ai (LLMExtractionStrategy + JSON schema)         │
│       T4: playwright + playwright-stealth + behavior_human       │
│       T5: browser-use (agente autonomo per flussi complessi)     │
│       SPECIAL: WebSearch/WebFetch di Claude (per email           │
│                enrichment, riusando la pipeline validata)        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Stack — scelte e perché

Ogni scelta è ancorata al ranking del vault: `[[🕷️ Web Scraping & SERP#🧱 Stack consigliato per AIO Pulse]]`. Niente di nuovo da imparare per Lorenzo.

| Layer | Strumento | Versione vault | Note |
|---|---|---|---|
| Frontend | Next.js 15 + React 19 + Tailwind 4 | esistente | Pannello cockpit + UI ricerca colleghi |
| Auth + DB + Realtime + Vector | Supabase free | nuovo | 500MB bastano: bulk Bolagsverket ~1.7M aziende attive svedesi è grande (~GB), va parcheggiato in DuckDB locale o in tabelle Postgres compresse selettive — solo PMI di interesse vanno in `companies`. |
| Backend agent | FastAPI + LangGraph | nuovo | Python (lo stack scraping già è Python). LangGraph perché il loop con human-in-the-loop (`wait_approval`) è il suo pattern nativo. |
| LLM reasoning | Groq Llama 3.3 70B free | nuovo | 30 req/min gratis. Per l'orchestratore basta. |
| LLM bulk | Ollama Llama 3.1 8B locale | nuovo | Per normalizzazione email/nomi su volumi. Zero costo. |
| Embeddings | Ollama `nomic-embed-text` | nuovo | Locale, 768-dim, zero costo. |
| **T0 Open Data — Bolagsverket bulk** | download diretto + parser custom | nuovo | Bulk `vardefulla-datamangder.bolagsverket.se`, CC-BY-4.0 gratis. **Pattern riscritto, no import da `oppna-bolagsdata` (AGPL).** Parse via DuckDB. |
| **T0 Open Data — Bolagsverket API REST** | httpx + chiave gratis "valuable data" | nuovo | Per fetch puntuali e bilanci. Quota mensile gratis sufficiente per nostro volume. |
| **T0 Open Data — apiverket.se** | `apiverket-mcp` come tool MCP | MIT, valutare | 120+ endpoint pubblici (SMHI, Trafikverket, SCB, Bolagsverket). Sandbox gratis. Da provare in Fase 5 come MCP. |
| SERP T1 | searxng self-host | 🔴 vault, da installare | AGPL OK per uso interno (decisione `[[🕷️ Scraping Lab#🚦 Decisioni aperte]]`). |
| SERP fallback | openserp self-host | 🔴 vault, da installare | Multi-engine, output Markdown. |
| HTTP T2 | httpx + BeautifulSoup + trafilatura | trafilatura 🟡 vault | Trafilatura per testo pulito post-fetch. |
| Crawler T3 | crawl4ai | 🔴 vault — "scelta principale" | `LLMExtractionStrategy` + JSON schema riusabile (decisione aperta `schema-first extraction`). |
| Browser T4 | playwright + playwright-stealth | 🔴 vault | Comportamento umano: bezier mouse + Poisson delay + typing variabile. |
| Browser autonomo T5 | browser-use | 🔴 vault | Per flussi che richiedono navigazione decisionale (form complessi, login). |
| Estrazione email speciale | Claude WebSearch + WebFetch sub-agent | validato vault | Pipeline B2B Contact Enrichment così com'è — viene chiamata come tool dall'orchestratore. |
| OSINT layer | awesome-hacker-search-engines reference | 🔴 vault | Quando l'agente RAG sceglie fonti per persone/domini specifici. |
| Job queue | Redis + RQ (locale via Docker) | nuovo | Semplice, Python-native, retry/backoff inclusi. |
| Memory long-term | Vault Obsidian (read + write `.md`) | esistente | L'agente legge `[[🕷️ Web Scraping & SERP]]` come playbook; scrive lezioni in `Workflows/scraping-runs/`. |
| **Observability (opzionale)** | LangSmith free tier | nuovo | 5k traces/mese gratis. Tracing visivo del grafo LangGraph in dev. **Solo opt-in**: senza `LANGCHAIN_API_KEY` il sistema gira normalmente. Disattivare prima di prod per non leakare prompt sensibili. |

### Cose deliberatamente *non* nello stack iniziale

- **teracrawl** (🟡 vault) — costi Browser.cash da verificare. Fuori MVP.
- **lightpanda** (🔴 vault) — per volumi milioni, non serve oggi.
- **PageIndex** — utile per PDF/documenti, non per il caso aziende svedesi. Sarà nel v2.
- **CAPTCHA solver dedicato** — strategia: **evitare** i CAPTCHA con T4 stealth + rate limit, non bypassarli. `nopecha-extension` (🟢 vault) resta solo come backup manuale.
- **Multi-agent swarm** (OpenSwarm, MiroThinker) — sovradimensionato. Un agente con sub-task è sufficiente.

---

## 5. Schema dati (Postgres)

```sql
-- entità canoniche
companies (
  id              uuid pk,
  org_nr          text unique,        -- chiave naturale svedese
  foretagsnamn    text not null,
  adress_gata     text,
  postnummer      text,
  stad            text,
  region          text,               -- es. "Skåne län"
  land            text default 'Sverige',
  reception_tel   text,
  email_info      text,
  hemsida         text,
  bransch         text,                -- categoria allabolag
  fatturato_mkr   numeric,
  num_dipendenti  int,
  vd_namn         text,                -- CEO/VD name
  skapad_datum    timestamptz default now(),
  senast_andrad   timestamptz default now()
)

contacts (
  id              uuid pk,
  company_id      uuid fk companies,
  namn            text not null,
  roll            text,                 -- "VD", "CTO", "Beslutsfattare"...
  telefon         text,
  email           text,
  email_verified  boolean default false,
  email_source    text,                 -- url della fonte
  email_method    text,                 -- "websearch", "webfetch", "linkedin"...
  is_dm           boolean,              -- decision maker
  notes           jsonb default '[]'    -- audit trail
)

sources (
  -- traccia provenienza per ogni campo aggiornato
  id              uuid pk,
  company_id      uuid fk,
  field_name      text,                 -- es. "contacts.email"
  source_url      text,
  scraper_tier    int,                  -- 1..5
  fetched_at      timestamptz,
  raw_excerpt     text                  -- contesto verificabile
)

scrape_jobs (
  id              uuid pk,
  plan_id         uuid fk plans,
  query           text,
  target_domain   text,
  tier_used       int,
  status          text,                 -- pending|running|done|blocked|failed
  result_count    int,
  blocked_reason  text,                 -- captcha|rate_limit|fingerprint...
  started_at      timestamptz,
  finished_at     timestamptz,
  cost_estimate   numeric default 0
)

plans (
  id              uuid pk,
  user_prompt     text,                 -- "trovami i CTO IT-konsulter Skåne"
  steps           jsonb,                -- array di step proposti
  status          text,                 -- draft|approved|executing|done
  created_at      timestamptz default now(),
  approved_at     timestamptz,
  approved_steps  jsonb                 -- subset che l'utente ha confermato
)

knowledge_chunks (
  id              uuid pk,
  kind            text,                 -- "playbook"|"snippet"|"query_log"|"lesson"
  content         text,
  embedding       vector(768),          -- nomic-embed-text
  metadata        jsonb,                -- {domain, tier, ts, source_file}
  vault_path      text                  -- se proveniente da vault Obsidian
)
```

### Migrazione da `localStorage`

Script one-shot Node: legge il JSON corrente, mappa `Company`/`Contact` esistenti al nuovo schema, `INSERT ON CONFLICT` su `org_nr` (quando presente) o `foretagsnamn` lowercase. Senza perdite.

---

## 6. L'agente — flusso LangGraph

```
                ┌──────────────────────┐
   user_prompt  │   RECALL (RAG)       │
   ─────────────▶  - query pgvector    │
                │  - read vault MD     │
                │  - read companies DB │
                └─────────┬────────────┘
                          ▼
                ┌──────────────────────┐
                │   PLAN (Groq 70B)    │
                │  emette JSON:        │
                │  [{query, source,    │
                │    tier, expected}]  │
                └─────────┬────────────┘
                          ▼
                ┌──────────────────────┐
                │ WAIT_APPROVAL        │ ◀── SSE → UI cockpit
                │ (human in the loop)  │ ───▶ utente: ✓/✗/edit
                └─────────┬────────────┘
                          ▼
                ┌──────────────────────┐
   ┌────────────▶   EXECUTE step       │
   │            │  enqueue → worker    │
   │            │  tier escalation auto│
   │            └─────────┬────────────┘
   │                      ▼
   │            ┌──────────────────────┐
   │            │ RECONCILE            │
   │            │  - Ollama normalizza │
   │            │  - dedup vs DB       │
   │            │  - regole verifica   │
   │            │    (pipeline B2B)    │
   │            └─────────┬────────────┘
   │                      ▼
   │            ┌──────────────────────┐
   │            │ CRITIC (opzionale)   │ ← ispirato da AutoGen 0.2
   │            │  - Ollama re-check   │   pattern researcher/executor/
   │            │  - chiede prove?     │   critic; gira con LLM locale
   │            │  - downgrade fiducia │   → zero costo
   │            │  - se sospetto:      │
   │            │    flag review umano │
   │            └─────────┬────────────┘
   │                      ▼
   │            ┌──────────────────────┐
   │            │ MEMORY_UPDATE        │
   │            │  - INSERT sources    │
   │            │  - write vault MD    │
   │            │    (Workflows/runs/) │
   │            │  - upsert knowledge  │
   │            └─────────┬────────────┘
   │                      ▼
   └───── more steps? ────┤
                          ▼
                       DONE
```

**Critic node — dettaglio (Fase 10b, opzionale)**: gira con Ollama Llama 3.1 8B locale dopo `RECONCILE`. Input: i record che `RECONCILE` ha prodotto + le regole di verifica (sezione 6.1). Output: per ogni record, una decisione `accept` / `flag_for_review` / `reject` con motivazione testuale salvata in `sources.critic_note`. Quando `flag_for_review`, il record entra nel DB con `verifierad=false` e una nota interna automatica per il review manuale. Costo: zero (Ollama locale).

### Regole di verifica (riusate dalla pipeline validata)

Ereditate da `[[🕷️ Web Scraping & SERP#🏆 Pipeline Validata]]`:

- ✅ email accettata solo se trovata **testualmente** in fonte pubblica
- ❌ rifiutate: `info@`, `kontakt@`, `hej@`, `hello@`, `post@`, `mail@`, `support@`, `admin@`
- ❌ rifiutate: email generate per pattern
- ❌ rifiutate: Gmail su domini corporate
- ❌ rifiutate: paywall-only / mascherate
- ✅ cross-dominio OK se azienda rinominata ufficialmente

Codificate come `pipeline/rules/email_verification.py` con test unitari sui casi reali del dataset esistente.

---

## 7. Memoria — quattro layer, una sola verità

```
┌─────────────────────────────────────────────────────────────┐
│  1. Postgres canonical (companies, contacts, sources)       │
│     → la verità sui dati                                    │
├─────────────────────────────────────────────────────────────┤
│  2. pgvector knowledge_chunks                               │
│     → snippet pagine, query log, lezioni embeddate          │
│     → query "ho già cercato qualcosa di simile?"            │
├─────────────────────────────────────────────────────────────┤
│  3. Vault Obsidian (`lorenzovault/Workflows/scraping-runs/`)│
│     → playbook leggibili da te, scritti dall'agente in MD   │
│     → ogni run ha la sua nota: query, esito, lezioni        │
│     → Lorenzo li riapre, modifica, raffina                  │
├─────────────────────────────────────────────────────────────┤
│  4. gbrain MCP (knowledge graph progetti/clienti)           │
│     → relazioni azienda↔progetto↔contatto a livello business│
│     → resta per uso strategico, non per scraping ops        │
└─────────────────────────────────────────────────────────────┘
```

**Scrittura nel vault da parte dell'agente**: solo in `Workflows/scraping-runs/YYYY-MM-DD-<slug>.md`. **Mai** modificare note esistenti del vault. Frontmatter standard con `#status/auto-generated`, link a `[[🕷️ Scraping Lab]]`. Lorenzo può promuovere una run a playbook spostandola/rinominandola — il pgvector si re-indicizza al watcher.

**Lettura dal vault**: l'agente embedda `[[🕷️ Web Scraping & SERP]]`, `[[🕷️ Scraping Lab]]`, `[[🔍 OSINT]]`, `[[🧠 AI Agents & Infrastructure]]` come **playbook chunks** nel knowledge_chunks all'avvio. Ri-sync su file change (watchdog).

---

## 8. Tier system e anti-bot — lezioni vault codificate

### Principio guida (aggiornato 2026-05-27): **prefer open data over scraping**

Dal febbraio 2025 Bolagsverket pubblica come **öppna data** (CC-BY-4.0) il dataset bulk delle aziende svedesi attive, in conformità con la Direttiva UE 2019/1024 sui High-Value Datasets. Riferimenti:

- Bulk SCB: `https://vardefulla-datamangder.bolagsverket.se/scb/scb_bulkfil.zip`
- Bulk Bolagsverket: `https://vardefulla-datamangder.bolagsverket.se/bolagsverket/bolagsverket_bulkfil.zip`
- API REST gratuite per "valuable data" — vedi `https://bolagsverket.se/apierochoppnadata`
- Aggregatore third-party con 120+ endpoint pubblici: `https://apiverket.se` (utile come MCP via `vinvuk/apiverket-mcp`, MIT)

L'orchestratore deve **sempre interrogarsi se il dato richiesto esiste già come open data ufficiale** prima di proporre uno scraping. Lo scraping resta per i campi che il bulk/API ufficiali non coprono (categoria bransch dettagliata di allabolag, fatturato/dipendenti se non in Bolagsverket, contatti email, ecc.).

**Reference repo studiati (non importati)**:

- [`PierreMesure/oppna-bolagsdata`](https://github.com/PierreMesure/oppna-bolagsdata) — AGPLv3 ⚠️, pattern di parsing bulk Bolagsverket. **Non importare** (AGPL copyleft di rete, contaminerebbe il progetto). Riscrivere il pattern (download + unzip + duckdb) sotto la nostra licenza.
- [`larsthelord/bolagsverket_ETL`](https://github.com/larsthelord/bolagsverket_ETL) — MIT, ETL DuckDB + PowerShell. Pattern di riferimento; manutenzione minima → non dipendere.
- [`onify/blueprint-bolagsverket-get-ssbten`](https://github.com/onify/blueprint-bolagsverket-get-ssbten) — ❌ **escluso**: SSBTEN richiede certificato TeliaSonera + accesso ristretto a soggetti pubblici svedesi (autorità/comuni/regioni). Savant Media privata → non qualificata.
- [`vinvuk/apiverket-mcp`](https://github.com/vinvuk/apiverket-mcp) — MIT, MCP per apiverket.se (aggregatore). Sandbox key gratis, prod a credenziali. **Valutazione**: utile come scorciatoia MCP nell'agente, ma la fonte canonica resta Bolagsverket diretta.

**Repo agentici valutati il 2026-05-27**:

- [`microsoft/autogen` branch 0.2 — `agentchat_web_info.ipynb`](https://github.com/microsoft/autogen/blob/0.2/notebook/agentchat_web_info.ipynb) — ⚠️ **Solo ispirazione**. Codice MIT, ma il branch 0.2 è in maintenance mode (autogen v0.4+ è la versione corrente, completamente riscritta) e il notebook usa **OpenAI gpt-4 a pagamento**. Importare autogen come dipendenza significherebbe una seconda macchina d'agenti accanto a LangGraph → complessità inutile. **Ispirazione adottata**: il pattern "researcher → executor → critic" è realizzato come nodo `critic` opzionale del nostro grafo LangGraph (vedi sezione 6).
- [`microsoft/autogen` notebook `agentchat_webcrawling_with_spider`](https://microsoft.github.io/autogen/0.2/docs/notebooks/agentchat_webcrawling_with_spider/) — ❌ **Scartato**. Usa il servizio `spider.cloud` (pay-as-you-go: $1/GB bandwidth + $0.001/min compute), free credits solo al signup. Viola il vincolo costo zero. La libreria OSS `spider-rs` (Rust, MIT) duplica `crawl4ai` (🔴 vault) e `lightpanda` (🔴) che abbiamo già nel piano → niente da aggiungere.
- [`langchain-ai/langgraph`](https://github.com/langchain-ai/langgraph) — ✅ **Confermato come scelta primaria**. MIT, framework completamente self-hostable gratis. Ecosistema: **LangSmith** ha tier free fino a 5k traces/mese (opzionale, utile in dev per debug visivo del grafo); **LangGraph Platform/Cloud** sono prodotti commerciali → ignorati. Documentazione human-in-the-loop diretta.

### Attribuzione (vincolo legale)

I dati provenienti da Bolagsverket/SCB sono CC-BY-4.0. Obbligo: **mostrare l'attribuzione** in UI e in qualsiasi export. Lo schema `sources` (sezione 5) registra già `source_url` e `raw_excerpt`; aggiungere un campo derivato `license_label` ("CC-BY-4.0", "scraped-private", ecc.) e una banda di attribuzione nell'header della UI quando un record visualizzato contiene campi CC-BY.

### Decision tree (da `[[🕷️ Scraping Lab#🧭 Decision tree]]`, raffinato)

```
Dato richiesto
   │
   ├─ è azienda svedese base       → T0 OPEN DATA
   │  (org.nr, nome, indirizzo,       bulk Bolagsverket + SCB (CC-BY-4.0)
   │  amministratori)?                opp. API REST Bolagsverket valuable data
   │
   ├─ è dato pubblico SE coperto   → T0 OPEN DATA via apiverket-mcp
   │  da altre agenzie (SMHI,         (Trafikverket, SCB, ecc.)
   │  Trafikverket, SCB)?
   │
   ├─ è una SERP query?            → T1 (searxng) → fallback openserp
   │
   ├─ è un sito statico/SSR?       → T2 (httpx+BS) → T3 (crawl4ai) se JS leggero
   │
   ├─ è una SPA / anti-bot leggero?→ T3 crawl4ai LLMExtractionStrategy
   │
   ├─ è anti-bot aggressivo        → T4 playwright-stealth + behavior_human
   │  o serve login persistente?      con context cookie persistito su disco
   │
   ├─ richiede navigazione         → T5 browser-use (agente sceglie click)
   │  decisionale (form complessi)?
   │
   └─ è enrichment email da elenco → SPECIAL: Claude WebSearch+WebFetch
      domini noti?                    sub-agent (pipeline B2B validata)
```

### Lezioni concrete già pagate (dal vault) — codificate nel codice

| Lezione | Dove va nel codice |
|---|---|
| `allabolag.se/foretag/*` è SPA React, non scrapabile T4 → usare solo `/bransch-sök` | `scrapers/allabolag/strategy.py`: blocca T4 su `/foretag/`, forza `/bransch-sök` |
| Cookie consent "Godkänn" su allabolag persiste nel context Playwright | `scrapers/_browser_context.py`: persistenza storage_state su disco |
| Numero dipendenti su allabolag è dietro paywall | Marker `unavailable_field` in schema, no retry |
| Filtro fatturato 5–500 Mkr come proxy PMI | Default config `allabolag.bransch_filter` |
| `duckduckgo-search` → `ddgs` | `requirements.txt` aggiornato |
| Batch 50 domini / 7 agenti paralleli | `config.MAX_PARALLEL=7`, `BATCH_SIZE=50` |
| Encoding utf-8 obbligatorio nei worker Python | `sys.stdout.reconfigure(encoding="utf-8")` in worker init |
| Tag `"Beslutsfattare"` significa DM, non eliminare | `pipeline/rules/dm_classification.py` |
| RocketReach: solo se email visibile senza paywall | `scrapers/rocketreach.py` con regola explicit |
| Dedup per email lowercase, non per dominio | `pipeline/dedup.py`, test su dataset 2548 reali |

### Comportamento umano — implementazione

In `scrapers/_human_behavior.py`:

```python
# delay: Poisson distribution invece di uniform → più umano
def human_delay(mean_seconds=2.5):
    import random, math
    # exponential = inter-arrival Poisson; clamp 0.5..10s
    d = -math.log(1.0 - random.random()) * mean_seconds
    return max(0.5, min(10.0, d))

# mouse: bezier curve a 3 punti di controllo
async def human_mouse_move(page, x, y):
    # 30..60 step lungo curva quadratica con jitter
    ...

# typing: variabile 80..180ms per char, pause su punteggiatura
async def human_type(page, selector, text):
    ...
```

Riferimento di policy: rispetto `robots.txt` di default (override esplicito per uso interno legittimo), `User-Agent` realistico aggiornato, rotazione headers. Niente proxy residenziali a pagamento nel v1 — se il limite di IP singolo morde, escalation manuale.

---

## 9. UI cockpit — pagine Next.js da aggiungere

| Route | Cosa fa |
|---|---|
| `/` esistente | Lista aziende, ricerca, filtri (esistente, da migrare al DB) |
| `/orchestrator` **nuova** | Input prompt → vede piano proposto → approva/edita step → segue esecuzione live (Realtime) |
| `/orchestrator/runs` **nuova** | Storico run con link al `.md` del vault generato |
| `/orchestrator/memory` **nuova** | Browser sui `knowledge_chunks`: cerca semanticamente lezioni passate |
| `/companies/[id]` esistente | Dettaglio + audit trail per ogni campo (chi/come/quando) |

Realtime via `supabase-js` channel su `scrape_jobs.status` → la card del job in UI cambia colore al volo.

---

## 10. Integrazione con `allabolag-scrape` esistente

Non riscrivere. **Adottare** il codice esistente:

1. Spostare `allabolag-scrape/scrapers/` → `backend/scrapers/` (con `git mv` se merge repo, o submodule)
2. Wrappare `scrapers/allabolag.py`, `merinfo.py`, `linkedin_serp.py`, `contact_finder.py` come **tool** richiamabili dall'orchestratore LangGraph
3. I `phase*.py` ad-hoc → **deprecati**: la stessa funzionalità diventa "step di un piano" generato dall'agente
4. `pipeline/scorer.py` e `pipeline/exporter.py` restano, esposti come step opzionali del piano
5. Dataset esistenti (`data/raw/companies.jsonl`, `LISTA_B_MASTER_SWEDISH_SMB_794_*.xlsx`) → script di import one-shot nel DB Postgres come **seed iniziale**, audit trail con `sources.scraper_tier=null, raw_excerpt='legacy_import'`

Risultato: tutto il valore già prodotto da `allabolag-scrape` confluisce nel DB Savant Media, e da lì in poi ogni nuova run passa dall'orchestratore.

---

## 11. Roadmap — fasi commitabili

> **Status al 2026-05-27 (sera, dopo 5 wave di subagent)**:
>
> | Fase | Stato | Note |
> |---|---|---|
> | 1 — Supabase schema | ✅ | Push fatto, 590 record reali nel DB |
> | 2 — Migrazione localStorage→Supabase | ✅ | `/admin/migrate` + Server Actions |
> | 2a/2b — Import bulk Bolagsverket | ✅ | 500 AB Stockholms län importati (T0 CC-BY-4.0) |
> | 3 — Import seed legacy | ✅ | 90 da Claude WebSearch + 500 da bulk = 590 totali |
> | 4 — Auth + RLS colleghi | ⏳ | Migration `0003_enable_rls_phase4.sql` scritta, NON applicata |
> | 4-bis — GRANTs public (deadline 2026-10-30) | ⏳ | Migration `0002_enable_public_grants.sql` scritta, NON applicata |
> | 5 — FastAPI scaffold + orchestrator endpoints | ✅ | 5/5 endpoint smoke test pass |
> | 6 — LangGraph base agent | ✅ | `recall → plan → save_plan → wait_approval → memory_update`, MemorySaver checkpointer |
> | 7 — UI `/orchestrator` cockpit | ✅ | 3 tab: Nytt plan / Aktiv plan / Senaste planer + Agent Memory link |
> | 8 — Worker T1+T2 SearXNG + httpx/BS+trafilatura | ✅ | Soft-fail su SearXNG down, Poisson delay, UA rotation |
> | 9 — Worker T3 crawl4ai | ✅ | `LLMExtractionStrategy` + JSON schema svedese, supporto Ollama+Groq |
> | 10 — Reconcile + email verification | ✅ | Regole pipeline B2B validata, 6/6 self-test pass |
> | 10b — Critic node (ispirato AutoGen 0.2) | ✅ | Ollama re-check con fallback rule-based |
> | 11 — Memory writer (vault MD + pgvector) | ✅ | Solo `Workflows/scraping-runs/`, `nomic-embed-text` 768-dim |
> | 12 — Worker T4 Playwright stealth | ✅ | Anti-detect init scripts, bezier mouse, storage_state persistito, lezioni vault codificate (`_allabolag_strategy.py`) |
> | 13 — Pipeline B2B enrichment as tool | ✅ | `enrich_b2b()` + `enrich_batch(max_parallel=7)`, regex emails + name-near-email + LinkedIn |
> | 14 — Worker T5 browser-use | ✅ | Autonomous agent (Ollama/Groq), soft-import (deps commentate, install on-demand) |
> | 15 — UI `/orchestrator/memory` | ✅ | Browse + keyword + semantic toggle + 4-color kind badge |
> | 16 — Hardening anti-bot | ✅ | `_robots.py` + `_rate_limit.py` + `_retry.py` + `policy.py::safe_fetch` + `HARDENING.md`. Default ON: robots respect, per-domain rate limit (allabolag 6 rpm, linkedin 4 rpm, bolagsverket 60 rpm), exp backoff con Retry-After, circuit breaker opzionale. Realistic viewport/UA/Accept-Language. Integrato in T2+T4. |
> | 17 — Deploy prod | ✅ | `docs/DEPLOY.md` (643 righe, 11 sezioni + 2 appendici) + `Dockerfile.frontend` + `backend/Dockerfile` + `docker-compose.yml` + `docker-compose.prod.yml` + `deploy/systemd/...service` + `deploy/nginx/...conf` + `deploy/README.md`. Stack ≈ 5€/mese (Hetzner CX22) o 0€ self-host con Tailscale. |
> | **EXECUTE wiring** ✅ — il salto da "agente che propone" a "agente che esegue" ⭐ | `backend/agent/executor.py` con dispatch per-tier T0-T5 + B2B enrichment special path; graph extended `wait_approval → execute_plan → reconcile_results → critic → memory_update → END`; FastAPI `/approve` con `BackgroundTasks` + `resume_execute_phase`; nuovo endpoint `GET /orchestrator/plans/:id/execution`; UI polling ogni 3s con ExecutionView. Defensive: ogni worker fallisce gracefully senza interrompere la sequenza. |
>
> **Manuale ancora da fare per Lorenzo**:
> 1. `supabase db push` per applicare `0002_enable_public_grants.sql` + `0003_enable_rls_phase4.sql`
> 2. `playwright install chromium` se vuole effettivamente usare T4/T5
> 3. `docker run searxng/searxng` se vuole T1 vivo
> 4. `pip install browser-use` quando vuole attivare T5
> 5. `python -m memory seed` per ingestare vault Obsidian → pgvector
>
> **Stack agente operativo end-to-end**: prompt utente in UI `/orchestrator` → Server Action `requestPlan` → FastAPI `POST /orchestrator/plan` → LangGraph (recall → plan → save → wait_approval) → DB write → UI mostra piano → Lorenzo approva → `POST /approve` → status='approved'. Esecuzione effettiva (T0-T5 + reconcile + critic + memory_update) sarà il prossimo ciclo di Fase 8 EXECUTE wiring.

Ogni fase è un PR atomico con criterio di "done" verificabile.

| # | Fase | Done quando | Stima |
|---|---|---|---|
| 1 | **Supabase setup + schema** | `migrations/0001_init.sql` applicata (con campo `license_label` su `sources`), RLS attive | 1 giorno |
| 2 | **Migrazione localStorage → Supabase** | UI esistente legge/scrive da Supabase, dati `localStorage` esistenti migrati | 1-2 giorni |
| 2a | **T0 Open Data — Import bulk Bolagsverket + SCB** ⭐ NEW | Bulk CC-BY-4.0 scaricato, parser DuckDB custom (no import AGPL), tabella `companies_se_registry` popolata (~1.7M righe), UI mostra banda attribuzione "Källa: Bolagsverket / SCB · CC-BY-4.0" | 2-3 giorni |
| 2b | **Promozione selettiva al catalogo PMI** ⭐ NEW | Filtri PMI (Stockholm/Skåne, fatturato 5–500 Mkr, 5–200 dip.) → `INSERT ... SELECT FROM companies_se_registry` con audit `tier=0, license=CC-BY-4.0` | 1 giorno |
| 3 | **Import seed da `allabolag-scrape`** | `LISTA_B_MASTER_*.xlsx` + `companies.jsonl` come **enrichment** sopra il registry (no più seed primario): match per org.nr, popola solo campi extra (bransch, fatturato, VD-email) | 1 giorno |
| 4 | **Auth + RLS colleghi read-only** | un secondo account vede i dati ma non scrive | 0.5 giorni |
| 5 | **Scaffold FastAPI + Redis Docker + apiverket-mcp** | `GET /health` OK, Redis raggiungibile, MCP gbrain + apiverket testati | 1.5 giorni |
| 6 | **Agente LangGraph base** (recall → plan → wait_approval, **senza execute**) | da prompt utente genera piano JSON, mostrato in UI, "approva" cambia status | 3-4 giorni |
| 7 | **UI `/orchestrator`** | input prompt + tabella step proposti + bottoni approve/edit + SSE status | 2-3 giorni |
| 8 | **Worker T1+T2** (searxng + httpx/BS+trafilatura) | un piano semplice "trova hemsida per N aziende" esegue end-to-end, scrive nel DB con audit | 3-4 giorni |
| 9 | **Worker T3 crawl4ai con LLMExtractionStrategy** | schema JSON `contact_extraction.schema.json` riusabile, test su 3 siti | 2-3 giorni |
| 10 | **Reconcile + regole verifica email** | regole `pipeline/rules/email_verification.py` con test sui 2548 reali | 2 giorni |
| 10b | **Critic node** (opzionale, ispirato AutoGen 0.2) | Ollama re-check post-reconcile, output `accept/flag/reject` + nota; record sospetti entrano con `verifierad=false` e nota interna auto | 1-2 giorni |
| 11 | **Memory writer** (vault `.md` + pgvector ingest) | dopo ogni run viene scritto un MD in `Workflows/scraping-runs/` + chunks embeddati | 2 giorni |
| 12 | **Worker T4 Playwright stealth + behavior_human** | scraping `allabolag/bransch-sök` riprodotto con tutte le lezioni cookie+stealth | 4-5 giorni |
| 13 | **Special: pipeline B2B email enrichment come tool** | l'agente può chiamare la pipeline WebSearch+WebFetch come step di un piano | 2-3 giorni |
| 14 | **Worker T5 browser-use** | un flusso login (LinkedIn pubblico via login proprio) funziona end-to-end | 3-4 giorni |
| 15 | **`/orchestrator/memory` UI** | ricerca semantica sui knowledge_chunks via UI | 1-2 giorni |
| 16 | **Hardening anti-bot** | Poisson delay, bezier mouse, typing variabile, rotazione UA, storage_state per dominio | 1 settimana iterativa |
| 17 | **Deploy prod** | colleghi accedono da `db.savantmedia.se` (Vercel/dominio), backend resta su macchina dev | 1-2 giorni |

**MVP utile = fasi 1-8** (compreso il T0 nuovo, fasi 2a-2b): in ~2-3 settimane hai un sistema dove **il catalogo di aziende svedesi è già nel DB da open data ufficiali** (1.7M attive, costo zero, legali) e l'agente arricchisce con scraping solo i campi mancanti, su approvazione esplicita.

---

## 12. Decisioni aperte — questo doc chiude, lascia, rilancia

### Chiuse da questo design

- ✅ **Unified scrape facade** (era aperta in `[[🕷️ Scraping Lab]]`) → si materializza come `/orchestrator` + agente LangGraph.
- ✅ **Schema-first extraction crawl4ai** → adottata come default per T3, schema JSON in `backend/scrapers/schemas/`.
- ✅ **searxng AGPL uso interno** → OK, gira solo sulla macchina dev, mai esposta a utenti SaaS.
- ✅ **Python vs Node per backend** → Python (FastAPI + LangGraph).
- ✅ **Memoria gerarchia** → 4 layer come sopra, vault Obsidian è playbook source.
- ✅ **T0 Open Data come default per aziende svedesi base** ⭐ — bulk Bolagsverket CC-BY-4.0 (gratis dal feb 2025 per Direttiva UE HVD) è la prima fonte. Lo scraping degrada a enrichment dei campi mancanti.
- ✅ **`oppna-bolagsdata` AGPL non importato** ⭐ — pattern riscritto in casa per evitare contaminazione AGPL della codebase. Stessa filosofia di searxng.
- ✅ **`onify/blueprint-bolagsverket-get-ssbten` escluso** ⭐ — SSBTEN richiede certificato TeliaSonera e qualifica di soggetto pubblico svedese. Savant Media privata → non utilizzabile.

### Lasciate aperte (da decidere prima di certe fasi)

- 🟡 **Docker Desktop** — prerequisito per searxng/crawl4ai/Redis. Installalo prima di Fase 5.
- 🟡 **GDPR policy scritta** per scraping aziende svedesi — da definire prima di Fase 12 (T4 attivo). Suggerimento: limitarsi a dati pubblici di società di capitali (org.nr, indirizzo registrato, VD pubblico), niente dati personali di privati cittadini.
- 🟡 **Migrazione SDK SerpAPI** (`google-search-results-python` → `serpapi`) — fuori MVP. Se serve SerpAPI come fallback in T1, farla allora.
- 🟡 **`chrome-devtools-mcp`** integrazione vault — utile come tool extra per debug scraper, non bloccante per il sistema.
- 🟡 **`apiverket-mcp` adozione** ⭐ — provarlo in Fase 5. Decidere se è abbastanza affidabile/aggiornato da dipenderci o se preferiamo chiamare le API ufficiali Bolagsverket/SCB direttamente. Il rischio: third-party che potrebbe sparire.
- 🟡 **Bolagsverket API REST a quota** — la nuova versione 4.6 (apr 2026) include `/organizations` valuable-data gratis e nuove operation a quota mensile. Decidere se sottoscrivere quota a pagamento per dati che il bulk non copre (bilanci finanziari?), o se affidarsi al bulk + scraping per quelli.

### Rilanciate (nuove decisioni che questo design impone)

- ❓ **Repo separato per il backend** (`savantmedia-database-backend/`) o monorepo (`backend/` accanto a `app/`)? Suggerisco **monorepo** per semplicità, separabile in seguito.
- ❓ **Dove gira il dev backend** — solo macchina Lorenzo, o un piccolo VPS interno (Hetzner CX22 ~5€/mese) per uptime maggiore? Per MVP: macchina Lorenzo. Se i colleghi vogliono job in coda quando tu non sei al PC: VPS.
- ❓ **Promozione delle "run" del vault a "playbook"** — manuale (Lorenzo sposta il file) o semi-automatica (agente propone "questa run ha funzionato 3 volte, promuovila")? Direi manuale all'inizio, automatizziamo se diventa ripetitivo.

---

## 13. Filosofia di progetto

In ordine di importanza:

1. **Riusare, non riscrivere** — l'esperienza è già pagata. Lo stack è già scelto. Le regole sono già scritte. Questo design le mette in opera, non le sostituisce.
2. **Human in the loop esplicito** — l'agente propone, Lorenzo approva. Mai esecuzione cieca, soprattutto in T4-T5 dove il rischio reputazionale (block, ban) è reale.
3. **Audit trail su ogni campo** — per ogni dato nel DB sappiamo chi/quando/come l'ha trovato. La fiducia dei colleghi nel sistema dipende da questo.
4. **Costo zero come vincolo di design** — quando una scelta è gratis-vs-pagata, gratis vince. Quando gratis non c'è, l'agente lo dice chiaramente e Lorenzo decide.
5. **Il vault è la memoria** — non costruire un sistema di knowledge management parallelo. Il vault Obsidian è già il knowledge management. L'agente lo legge come playbook e scrive le sue lezioni dentro al suo perimetro (`Workflows/scraping-runs/`).
6. **Anti-fragile, non anti-bot** — non rincorrere CAPTCHA solver. Restare sotto la soglia di detection è più economico, più etico, più sostenibile.

---

## 14. Riferimenti vault

- `[[🕷️ Web Scraping & SERP]]` — catalogo strumenti
- `[[🕷️ Scraping Lab]]` — environment hub, decision tree
- `[[🧠 AI Agents & Infrastructure]]` — framework agenti
- `[[🔍 OSINT]]` — fonti per ricerca persone/aziende
- `[[🕷️ Web Scraping & SERP#🏆 Pipeline Validata]]` — pipeline B2B Contact Enrichment
- `[[🕷️ Web Scraping & SERP#🏗️ Progetto Attivo — allabolag-scrape]]` — predecessore da assorbire
- `[[⚙️ Setup & Integrazioni]]` — config tecnica vault
- `[[🔐 Security & API Keys]]` — chiavi (Supabase, Groq da aggiungere)
