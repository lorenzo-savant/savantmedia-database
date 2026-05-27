# Anti-bot hardening — Fase 16

> **Philosophy: anti-fragile, not anti-bot.**
> We stay below the detection threshold rather than try to bypass it. No
> CAPTCHA solvers, no residential proxy budgets, no arms race. Just
> well-paced, well-identified, robots-respecting fetches.

Reference: `docs/ARCHITECTURE.md` §13 ("Anti-fragile" — il manifesto), §8
(decision tree T0→T5).

---

## What this layer adds

Three primitives + an orchestrating policy wrapper:

| Module | Role | Singleton |
|---|---|---|
| `_robots.py`     | robots.txt fetcher + cache (urllib.robotparser, 1h TTL, fail-open) | `robots_policy` |
| `_rate_limit.py` | Per-domain token bucket (rpm + burst, env-tunable, vault overrides) | `rate_limiter` |
| `_retry.py`      | Exponential backoff + jitter, 429 Retry-After aware; per-host circuit breaker | `circuit_breaker` |
| `policy.py`      | `safe_fetch()` — wires robots + breaker + rate limit + human_delay + retry around any fetcher | — |

The lower-level scraper tiers (T2 `httpbs.py`, T4 `playwright_t4.py`) embed
the same checks internally and stay backward-compatible — callers that
already use them keep working, with the hardening applied by default.

T3 (`crawl4ai_worker.py`) and T5 (`browseruse_t5.py`) are agent-driven —
their internal LLM loop handles its own delays — and are not auto-wrapped.
If you need hardening around them, call `safe_fetch(url, fetcher_callable=crawl_and_extract, ...)`.

---

## Default policy (what gets enforced without you doing anything)

When you call `fetch_and_extract(url)` (T2) or `stealth_fetch(url)` (T4):

1. **robots.txt check** — host `/robots.txt` is fetched, cached 1h, and the
   URL is checked against the `*` (or your specified) user agent. Disallowed
   → returns `ScrapeResult(error="robots.txt disallow ...")` immediately.
   *On fetch failure (DNS / 5xx / timeout): fail OPEN with a one-time warning.*
2. **Per-domain token bucket** — default 30 rpm / burst 5; lower for known
   sensitive hosts (see table below). Caller awaits a token before fetching.
3. **Poisson human delay** — mean 2.5s (T2) / 2.0s (T4) on top of the bucket.
4. **Exponential backoff retry** — 3 attempts on HTTP 429 / 5xx / network
   errors, base 2s, jittered ±20%, honouring `Retry-After` on 429 (capped 60s).

Calling `safe_fetch(url, fetcher_callable=...)` adds:

5. **Circuit breaker** — 5 consecutive failures within 5 min → opens for 60s,
   then half-open with single probe. Prevents amplification storms on dead hosts.

---

## Per-domain rate limit overrides (vault-derived)

| Host | rpm | burst | Why |
|---|---:|---:|---|
| `allabolag.se` | 6 | 2 | Vault lesson: bursts > 2/min trigger soft block. `/foretag/` is SPA dead end anyway — only `/bransch-sök/` is scrapable. |
| `linkedin.com`, `www.linkedin.com` | 4 | 1 | Extremely sensitive. Public profile pages only. Never crawl logged-in areas. |
| `*.bolagsverket.se` | 60 | 10 | CC-BY-4.0 open data since Feb 2025. Generous because they want adoption. |
| *(default)* | 30 | 5 | Conservative baseline for everything else. |

Override at runtime:

```python
from scrapers._rate_limit import rate_limiter
rate_limiter.set_limit("example.se", rpm=15, burst=3)
```

Or via env (process-wide defaults):

```
SCRAPE_RPM_DEFAULT=30
SCRAPE_BURST_DEFAULT=5
```

---

## When (if ever) to use `ignore_robots=True`

**Only:**

- Testing your own domain (`savantmedia.se`).
- A site where you have written permission to scrape.
- A throwaway diagnostic run against a server you control.

**Never:**

- Production scraping of third-party sites.
- "Just this one time" against an allabolag.se / hitta.se / linkedin.com URL.
- To clear a robots.txt block you don't understand — read the file first.

`ignore_robots=True` is logged at WARN level every call so an audit can
spot misuse.

---

## Vault lessons recap (`🕷️ Web Scraping & SERP`)

Codified across this layer and `_allabolag_strategy.py`:

- **Cookie consent persistenza** — T4's `storage_state` keeps "Godkänn"
  cookies across runs; saves a click + a fingerprint event per fetch.
- **Allabolag `/foretag/<orgnr>`** — React SPA, content never renders even
  through T4. Use T0 (Bolagsverket open data) for org.nr lookups.
  `AllabolagStrategy.should_use_t4()` returns False for this path.
- **Batch sizes** — vault recommends 50 records per batch, 7 parallel
  workers max. The rate limiter enforces per-host caps regardless of
  parallel worker count, so 7 workers hitting allabolag still see 6 rpm.
- **Realistic fingerprints** — viewport rotates among real screen sizes
  (1920x1080 / 1366x768 / 1536x864 / 1440x900) instead of pinned 1366x768.
  Accept-Language is locale-aware (`sv-SE` by default for SE targets).
- **Scroll like a reader** — `realistic_scroll_pattern()` produces varied
  steps (200-500px), pauses (0.4-1.8s), and ~15% backtrack probability.
  Beats the old "scroll N times, fixed step" pattern.

---

## GDPR + ethics

Scraping is constrained to:

- **Open business data** — company names, org.nr, public contact info,
  board composition. All under Bolagsverket / SCB licences (CC-BY-4.0
  since Feb 2025).
- **Publicly-listed staff identifiers** — "VD: Anna Andersson"
  visible on a company "Om oss" page is public; what's behind a login is not.
- **No personal-data scraping at scale.** Per GDPR Art. 6, this dataset's
  legal basis is "legitimate interest" for B2B contact, and that interest
  doesn't cover trawling LinkedIn for non-business contact details.

If a target's robots.txt disallows our access, we honour it. If we have a
legitimate B2B reason to access a specific URL anyway, the workflow is:
contact the site operator with a published email, get written permission,
then `ignore_robots=True` with an audit-log entry — not "shrug and override."

---

## Quick reference — the recommended call

```python
from scrapers.policy import safe_fetch
from scrapers.httpbs import fetch_and_extract
from scrapers.playwright_t4 import stealth_fetch

# T2 with full hardening:
result = await safe_fetch(
    "https://example.se/kontakt",
    fetcher_callable=fetch_and_extract,
)

# T4 with full hardening + storage state:
result = await safe_fetch(
    "https://www.allabolag.se/bransch-sök/it-konsult",
    fetcher_callable=stealth_fetch,
    storage_state_key="allabolag",
    screenshot=True,
)

# Result is always a ScrapeResult — check .ok and .error.
if not result.ok:
    print(f"blocked: {result.error}")
```
