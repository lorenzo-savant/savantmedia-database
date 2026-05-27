# `backend/scrapers/` — Tier 1 + Tier 2 + Tier 3 + Tier 4 + Tier 5 workers

Library modules that the LangGraph orchestrator (Fase 6+) calls when its plan
needs to *fetch new data from the open web*. Each tier returns a uniform
[`ScrapeResult`](./base.py) so the orchestrator stays tier-agnostic.

See `docs/ARCHITECTURE.md` §8 for the full tier system and the decision tree
(prefer open data over scraping).

## Tier system (this module covers T1 → T5)

| Tier | Tool                                       | When to use                                                                   |
|-----:|--------------------------------------------|--------------------------------------------------------------------------------|
| T0   | Open data (Bolagsverket / SCB / apiverket) | Swedish company base data (org.nr, name, address, board, financial filings)  |
| T1   | **SearXNG** self-host (this module)        | SERP queries — find URLs, snippets, "where is X mentioned on the open web"   |
| T2   | **httpx + BS4 + trafilatura** (this module)| Static / SSR pages — clean text extraction for LLM and regex                  |
| T3   | **crawl4ai** (this module)                 | JS-rendered pages + LLM-driven structured extraction                          |
| T4   | **Playwright stealth + human** (this module) | Anti-bot, persistent login/consent sessions, mouse+typing realism            |
| T5   | **browser-use agentic browser** (this module) | Decision-driven flows: complex forms, multi-step logins, agent-chosen clicks |

## Files

- `base.py` — `ScrapeResult` Pydantic model + `to_source_audit()` projection
  for `public.sources` rows.
- `searxng.py` — async `SearXNGClient` (T1).
- `httpbs.py` — async `fetch_and_extract()` (T2): httpx + BeautifulSoup +
  trafilatura, with UA rotation and Poisson delays.
- `crawl4ai_worker.py` — async `crawl_and_extract()` (T3): Playwright via
  crawl4ai, optional `LLMExtractionStrategy` for JSON-schema-driven output.
- `playwright_t4.py` — async `stealth_fetch()` (T4): direct Playwright with
  anti-detection init script, Bezier mouse paths, human typing cadence, and
  persistent `storage_state` for sites that need a logged-in/consented session.
- `browseruse_t5.py` — async `autonomous_navigate()` (T5): wraps the
  `browser-use` framework — an LLM decides what to click / type, browser-use
  executes against Chromium. Used only when T4 fails AND the flow requires
  reasoning (multi-step forms, conditional navigation, agent-chosen buttons).
- `_llm_provider.py` — shared `get_llm(provider)` factory returning a
  langchain-compatible chat model (`ChatOllama` / `ChatGroq`). Used by T5
  today, planned for a T3 refactor that consolidates LLM config.
- `_allabolag_strategy.py` — per-host hints codified from the vault
  (`AllabolagStrategy`): which paths are scrapable, which are SPA dead ends,
  and the Swedish cookie-consent selector battery.
- `schemas/swedish_contact.py` — JSON-schema-style dicts for T3 LLM
  extraction: `SWEDISH_CONTACT_EXTRACTION_SCHEMA` (full kontakt/Om oss
  pages) and `SWEDISH_BRANSCH_EXTRACTION_SCHEMA` (allabolag/proff summary).
- `_human_behavior.py` — shared utilities: `human_delay`, `random_user_agent`,
  `is_swedish_business_domain`, `bezier_path`, `typing_cadence`,
  `realistic_scroll_pattern`, `realistic_viewport`, `realistic_accept_language`.
- `_robots.py` — async robots.txt parser + 1h cache (`robots_policy` singleton).
- `_rate_limit.py` — per-domain token bucket (`rate_limiter` singleton).
- `_retry.py` — `with_retry()` (exp backoff + 429 Retry-After) + `CircuitBreaker`.
- `policy.py` — `safe_fetch()` recommended wrapper that wires it all together.
- `HARDENING.md` — anti-fragile philosophy, default policy, per-domain limits,
  GDPR + ethics. **Read this before adding any new scraping target.**
- `cli.py` — typer CLI for manual debugging (`python -m scrapers.cli ...`).

## How to run SearXNG locally (T1)

SearXNG is AGPLv3 → run it self-hosted on the dev machine only, never expose
it to SaaS users (per architecture §10).

```bash
docker run -d \
    -p 8888:8080 \
    --name searxng \
    -e "BASE_URL=http://localhost:8888/" \
    -e "INSTANCE_NAME=savantmedia-dev" \
    searxng/searxng:latest

# Enable JSON output (default install is HTML-only):
docker exec -it searxng sh -c \
    "sed -i 's/- html/- html\n    - json/' /etc/searxng/settings.yml \
    && kill -HUP 1"
```

Then either set `SEARXNG_URL=http://localhost:8888` in `backend/.env`, or
rely on the default. Stop it with `docker stop searxng`.

## CLI examples

From `backend/` with the venv active:

```bash
# T1 — meta-search
python -m scrapers.cli searxng "ICA Gruppen organisationsnummer"
python -m scrapers.cli searxng "VD IT-konsult Skåne" --engines google,duckduckgo --limit 5

# T2 — fetch + extract a single URL
python -m scrapers.cli fetch "https://example.com"
python -m scrapers.cli fetch "https://www.scb.se/" --text-only
python -m scrapers.cli fetch "https://example.com" --no-delay  # skip Poisson wait (testing only)

# T3 — crawl4ai (JS rendering + optional LLM extraction)
python -m scrapers.cli crawl4ai "https://example.com"                                  # markdown only
python -m scrapers.cli crawl4ai "https://savantmedia.se/kontakt" --schema swedish_contact
python -m scrapers.cli crawl4ai "https://www.allabolag.se/5560021000" --schema swedish_bransch --llm groq

# T4 — Playwright stealth (anti-bot + persistent sessions + human behaviour)
python -m scrapers.cli playwright "https://example.com"
python -m scrapers.cli playwright "https://www.allabolag.se/bransch-sök/..." --storage-key allabolag --screenshot
python -m scrapers.cli playwright "https://hitta.se/" --no-headless        # visible browser for debugging
python -m scrapers.cli playwright "https://example.com" --wait-for-selector ".company-list" --timeout 90

# T5 — browser-use autonomous agent (decision-driven flows)
python -m scrapers.cli browseruse "Open foretag.se/kontakt and find the CFO's email"
python -m scrapers.cli browseruse "Hitta org.nr för Spotify AB" --start-url https://www.allabolag.se/ --llm groq
python -m scrapers.cli browseruse "Fyll i kontaktformuläret" --no-headless --max-steps 30
```

Every result is JSON unless `--text-only` is passed, so you can pipe to `jq`:

```bash
python -m scrapers.cli searxng "telia ab" | jq '.[].url'
```

## How to set up T3 — crawl4ai

crawl4ai wraps Playwright with LLM-friendly affordances (clean markdown,
`LLMExtractionStrategy`). Use it when:

- The page is JS-rendered (SPA, React/Vue/Svelte client-side routing).
- You need *structured* extraction (named fields, not just clean text) and
  T2 + regex isn't enough.
- The anti-bot stance is light (Cloudflare 'I'm Under Attack' belongs to T4).

For SSR / static pages, **stay on T2** — it's cheaper, faster, and gives
trafilatura's better text extraction than Playwright's DOM dump.

### Install

From `backend/` with the venv active:

```bash
# 1. Install the Python lib (heavy — pulls Playwright as transitive dep)
.venv/Scripts/python.exe -m pip install crawl4ai

# 2. Install the headless Chromium browser (one-off, ~150 MB)
.venv/Scripts/python.exe -m playwright install chromium
# OR — same thing, but crawl4ai's wrapper:
.venv/Scripts/crawl4ai-setup.exe
```

If `pip install crawl4ai` fails (network, build deps, transient PyPI 5xx),
the rest of the `scrapers` module still imports fine — `crawl4ai_worker.py`
soft-imports crawl4ai and `crawl_and_extract()` returns a `ScrapeResult`
with `error` populated, so T1/T2 keep working.

### LLM provider (optional, only when `--schema` is set)

- **Ollama (default, cost-zero)** — local LLM. Requires Ollama running on
  `http://localhost:11434` with the model in `OLLAMA_MODEL_REASONING`
  (default `llama3.1:8b`) pulled. Install: https://ollama.com
- **Groq (cloud, pay-per-token)** — set `GROQ_API_KEY` in `backend/.env`.
  Model defaults to `llama-3.3-70b-versatile`.

If the chosen LLM provider can't be configured (no API key, no LLMConfig in
this crawl4ai version), the worker falls back to markdown-only and records
the reason in `metadata["llm_fallback_reason"]` — it does not raise.

## How to set up T4 — Playwright stealth

T4 is the escalation tier when T2/T3 fail or when the site needs a
*persistent* logged-in / consented session that crawl4ai's stateless runs
throw away. It drives Playwright directly, injects an anti-detection init
script, and weaves human-like mouse + typing behaviour around the
navigation.

### When to use T4

- T2 returned 403 / 503 / Cloudflare challenge HTML.
- T3 ran but came back empty because the site killed Playwright on
  fingerprint heuristics (rare — most Swedish targets aren't that hardened).
- You need cookies / consent / login state to **survive across calls**
  (allabolag's "Godkänn" banner, hitta.se's "use Swedish" prompt, etc.).
- You need fine-grained interaction (forms, mouse moves, scroll-triggered
  loads) that the crawl4ai wrapper can't express cleanly.

For everything else, **stay on T2/T3** — T4 is heavier and slower.

### Install

The Python `playwright` package is already in the venv (transitive dep via
crawl4ai). The Chromium browser binary itself is a separate ~150 MB
download:

```bash
.venv/Scripts/python.exe -m playwright install chromium
```

Without it, `stealth_fetch()` returns a `ScrapeResult` with `error`
populated rather than crashing.

### Vault lessons codified

`_allabolag_strategy.py` captures hard-won lessons from Lorenzo's vault
(`🕷️ Web Scraping & SERP#🏗️ allabolag-scrape`):

- `AllabolagStrategy.should_use_t4(url)` — returns False for
  `/foretag/<orgnr>` (React SPA, content never renders even via T4 →
  prefer T0 Bolagsverket/SCB open data). Returns True only for
  `/bransch-sök/...` list pages, which are SSR and scrapable.
- `AllabolagStrategy.cookie_button_selectors()` — ordered list of
  Swedish-language "Godkänn"/"Acceptera" selectors plus common cookie-banner
  IDs. The T4 worker iterates them and stops at the first match, then
  persists the consent cookie via `storage_state` so the next call skips
  the banner entirely.

### Human behaviour implementation

- **`bezier_path((sx, sy), (ex, ey), steps)`** in `_human_behavior.py`
  builds a quadratic Bezier with a random perpendicular control point and
  ~1.5 px Gaussian per-step jitter. The T4 worker calls it for every mouse
  move so cursor traces show curvature, not straight lines.
- **`typing_cadence(text)`** returns per-character delays: 80-180 ms
  uniformly, plus an extra 200-400 ms pause on `. , ; : !`. Used by
  `_human_type()` for any form fill.
- **`human_delay(mean_seconds)`** (Poisson / exponential inter-arrival,
  clamped to [0.5, 10] s) gates the navigation itself so parallel workers
  don't stampede.
- **Anti-detection init script** spoofs `navigator.webdriver`,
  `navigator.languages` (`["sv-SE", "sv"]`), `navigator.plugins`, and
  ensures `window.chrome` exists. Kept small on purpose — large payloads
  are themselves a fingerprint.
- **Persistent `storage_state`** lands at
  `backend/data/storage/<key>.json`; screenshots at
  `backend/data/screenshots/<UTC-timestamp>.png`. Both directories are
  gitignored.

### CLI examples

```bash
# Smoke test — anonymous, no persisted state
python -m scrapers.cli playwright "https://example.com" --text-only

# Allabolag bransch-sök with persistent consent + screenshot for audit
python -m scrapers.cli playwright \
    "https://www.allabolag.se/bransch-sök/..." \
    --storage-key allabolag --screenshot

# Wait for a specific selector before grabbing content (SPA)
python -m scrapers.cli playwright \
    "https://example.com/app" \
    --wait-for-selector ".company-list" --timeout 90

# Visible browser — manually inspect what the bot sees during dev
python -m scrapers.cli playwright "https://hitta.se/" --no-headless
```

### WARNING — politeness, robots.txt, and rate limits

T4 is the strongest tool in this toolbox. **Use it sparingly and
respectfully.** Per `docs/ARCHITECTURE.md` §8, Lorenzo's policy is:

- **Prefer open data (T0) every time it covers the field.** Bolagsverket
  and SCB are CC-BY-4.0 since Feb 2025 — there is no excuse for scraping
  what they already give us.
- **Respect robots.txt.** The current implementation does not enforce it
  at the request layer; the orchestrator MUST check robots before
  dispatching a T4 step.
- **Rate limit per host.** Even with `human_delay`, parallel fan-out can
  hammer a single host. Serialise per-host fetches in the orchestrator —
  in-process token bucket is on the TODO list below.
- **Identify honestly when asked.** Several Swedish sites have a published
  contact for scraping requests — use it before going stealth.
- **Never use T4 against authenticated areas you don't have permission
  for.** This is non-negotiable.

T4 exists for the cases where T1-T3 cannot reach legitimately public
content. It is not a license to bypass anti-bot for fun.

## How to set up T5 — browser-use autonomous agent

T5 is the *last resort*. It wraps `browser-use`
(https://github.com/browser-use/browser-use, ⭐ 93k, 🔴 alta priority in
Lorenzo's vault — `[[🕷️ Web Scraping & SERP#browser-use]]`) — a layer
between an LLM and Chromium where the LLM decides "click this / type
that / extract this field" and browser-use executes the action.

### When to use T5

Only when **all of these hold**:

- T4 already failed *or* you've reasoned it would fail. The page itself
  loads fine — what's hard is the *navigation*.
- The flow requires reasoning (button choices, form filling with
  conditional logic, multi-page login, "click whichever result matches
  this person's title"). A scripted T4 selector chain can't express it.
- The natural-language goal is small and well-defined. "Find the CFO's
  email on this contact page" — yes. "Explore the entire site for
  anything interesting" — no, T5 will burn tokens and wander.

**Default to lower tiers.** T5 is an order of magnitude more expensive
in tokens *and* slower than T4. Treat it the way you'd treat a paid
API: budget it, justify it, log every step.

### Install

```bash
.venv/Scripts/python.exe -m pip install "browser-use[ollama]"
# or, if you'd rather drive it with Groq:
.venv/Scripts/python.exe -m pip install "browser-use[groq]"

.venv/Scripts/python.exe -m playwright install chromium
```

The Python package is gated behind a soft import — without it,
`autonomous_navigate()` returns a `ScrapeResult` with `error` set rather
than crashing. The dependencies are listed (commented out) in
`backend/requirements.txt` so a future `pip install -r requirements.txt`
won't accidentally pull the heavy agent + langchain stack.

### LLM choice

- **Ollama (default, cost-zero)** — local LLM. Reads `OLLAMA_BASE_URL`
  (default `http://localhost:11434`) and `OLLAMA_MODEL_REASONING`
  (default `llama3.1:8b`). Slower, but $0/run. Recommended for
  everything that isn't time-critical.
- **Groq (cloud, free tier today)** — pinned to
  `llama-3.3-70b-versatile`. Reads `GROQ_API_KEY` from `backend/.env`.
  Faster reasoning loops, but watch the free-tier quota.

Both paths use the shared `_llm_provider.get_llm(provider)` factory, so
adding a third backend (Anthropic, OpenAI, …) is a one-function patch.

### Concrete examples

```bash
# Find a person's current employer (LLM + browser-use navigates LinkedIn-style)
python -m scrapers.cli browseruse \
    "Navigera till linkedin.com/in/anna-lindberg och hitta hennes nuvarande arbetsgivare"

# Search allabolag for a company, open the result, extract org.nr + VD
python -m scrapers.cli browseruse \
    "Öppna allabolag.se, sök efter 'Spotify AB', öppna första resultatet, hämta org.nr och VD-namn" \
    --max-steps 25
```

### WARNING — same anti-bot policy as T4

T5 is T4 with an LLM in the loop — it inherits every constraint. Per
`docs/ARCHITECTURE.md` §8 / §10:

- **Respect robots.txt.** The agent doesn't check it; the orchestrator
  must check before dispatching a T5 step.
- **Per-host rate limits.** A wandering agent can rack up dozens of
  page loads in one task — serialise per-host fetches upstream.
- **No aggressive scraping.** T5 is for decision-driven flows on
  legitimately public content, not for bypassing logins or licence
  walls.
- **Token + wall-clock budgets.** Cap `max_steps` and `timeout` on
  every call. The defaults (20 steps / 180 s) are upper bounds, not
  targets.

Vault reference: `[[🕷️ Web Scraping & SERP#browser-use]]`.

## Integrating with the orchestrator (Fase 6+)

The LangGraph `EXECUTE` node will dispatch on `plan_step["tier"]`:

```python
from scrapers.searxng import SearXNGClient
from scrapers.httpbs import fetch_and_extract
from scrapers.crawl4ai_worker import crawl_and_extract
from scrapers.playwright_t4 import stealth_fetch
from scrapers.browseruse_t5 import autonomous_navigate
from scrapers.schemas.swedish_contact import SWEDISH_CONTACT_EXTRACTION_SCHEMA

async def execute_step(step: dict) -> ScrapeResult:
    if step["tier"] == 1:
        client = SearXNGClient()
        return (await client.search(step["query"], limit=10))[0]
    if step["tier"] == 2:
        return await fetch_and_extract(step["url"])
    if step["tier"] == 3:
        return await crawl_and_extract(
            step["url"],
            extraction_schema=SWEDISH_CONTACT_EXTRACTION_SCHEMA
                if step.get("extract") == "contact" else None,
        )
    if step["tier"] == 4:
        return await stealth_fetch(
            step["url"],
            storage_state_key=step.get("storage_key", "default"),
            wait_for_selector=step.get("wait_for_selector"),
            screenshot=step.get("screenshot", False),
        )
    if step["tier"] == 5:
        return await autonomous_navigate(
            task=step["task"],
            start_url=step.get("url"),
            llm_provider=step.get("llm", "ollama"),
            max_steps=step.get("max_steps", 20),
        )
    raise NotImplementedError(f"Tier {step['tier']} not yet wired")
```

After each step the `RECONCILE` node should call
`result.to_source_audit(field_name=...)` and insert the dict (plus the
`company_id` it just reconciled) into `public.sources` so the audit trail
stays complete.

## Hardening (Fase 16 — see [HARDENING.md](./HARDENING.md))

Anti-fragile, not anti-bot. The defaults below run automatically on T2 / T4
and via `safe_fetch()` for any tier:

- **robots.txt** enforced by default (fail-open on fetch errors, 1h cache).
  Opt out per-call with `ignore_robots=True` (logged WARN).
- **Per-domain token bucket** — default 30 rpm / burst 5. Overrides:
  allabolag.se 6/2, linkedin.com 4/1, bolagsverket.se 60/10.
- **Exponential backoff** on 429/5xx + network errors (3 attempts, ±20% jitter,
  honours `Retry-After` capped at 60s).
- **Circuit breaker** (via `safe_fetch`): 5 fails in 5min opens for 60s.
- **Realistic fingerprint** — viewport rotates (1920×1080 / 1366×768 /
  1536×864 / 1440×900) instead of pinned 1366×768. `Accept-Language`
  defaults to `sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7`.
- **Reader-shaped scroll** — `realistic_scroll_pattern()` (varied steps,
  pauses, occasional backtrack) replaces fixed 2-4 scrolls in T4.

Recommended call:

```python
from scrapers.policy import safe_fetch
from scrapers.httpbs import fetch_and_extract

result = await safe_fetch(url, fetcher_callable=fetch_and_extract)
```

`safe_fetch` adds the circuit breaker on top of what T2/T4 already enforce
internally. Direct calls to `fetch_and_extract` / `stealth_fetch` still get
robots + rate-limit + retry + human delay by default.

## Polite scraping defaults

- **User-Agent rotation**: every T2 request picks from 6 realistic browser
  UAs (Chrome/Firefox/Edge/Safari across Win/macOS/Linux).
- **Poisson delays**: default mean 2.5s between fetches, exponential
  inter-arrival → bursty + human-looking. Clamped to `[0.5, 10.0]s`.
- **Noscrape heuristic**: T2 refuses URLs containing obvious patterns like
  `/login`, `/checkout`, `/cdn-cgi/challenge-platform`. Complemented by
  full `robots.txt` parsing in `_robots.py`.
- **Swedish locale headers**: T2 sends `Accept-Language: sv-SE,sv;q=0.9,...`
  via `realistic_accept_language("SE")` (other locales available).

## Known limitations / TODO

- Rate limiter is in-process only. Multi-worker deployments need a shared
  store (Redis) — deferred until proven necessary per design doc.
- No on-disk response cache. Cheap to add via `hishel` if T2 starts
  hammering the same URLs.
