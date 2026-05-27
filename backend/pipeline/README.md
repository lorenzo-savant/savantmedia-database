# Reconcile + Enrichment pipeline (Fase 10 / 10b / 13)

This package implements the Reconcile + Critic stage *and* the validated
B2B Contact Enrichment tool described in `docs/ARCHITECTURE.md` §6, §8 and §11.

Modules:

| Module                 | Role                                                                 |
| ---------------------- | -------------------------------------------------------------------- |
| `email_verification`   | Pure rule engine — no LLM, no I/O. Mirrors `lib/utils.ts` `checkEmail`. |
| `reconcile`            | Applies the rules + LinkedIn/source heuristics to a batch of contacts. |
| `critic`               | LangGraph node — local Ollama LLM with deterministic rule fallback.  |
| `_extract_emails`      | Pure regex helpers: emails, name-near-email, LinkedIn profile URL.   |
| `b2b_enrichment`       | Async tool — orchestrates T1 (SearXNG) + T2 (httpx) + reconcile + critic. |

## Origin of the rules

The validation rules come straight from Lorenzo's B2B Contact Enrichment
playbook (`lorenzovault/Projects/🕷️ Web Scraping & SERP.md` → "Pipeline
Validata"), already used to validate **292 out of 548 leads**:

- **Accept** — email found textually in a public source; cross-domain only
  if the company officially renamed; RocketReach only when address is
  visible without paywall.
- **Reject** — generic locals (`info@`, `kontakt@`, `hej@`, `hello@`,
  `post@`, `mail@`, `support@`, `admin@`, …); pattern-generated emails;
  Gmail / Hotmail / Yahoo on a corporate identity; masked / paywall-only
  addresses.

The extended generic-local set lives in
`GENERIC_EMAIL_LOCALS` (matches the frontend constant in `lib/types.ts`).

## Where the Critic node fits in the agent graph

```
scrape  ─▶  reconcile  ─▶  critic  ─▶  memory_update  ─▶  persist
            (rules)       (Ollama or
                          rule fallback)
```

- The Critic receives `reconciled: list[ReconcileResult]` in `state` and
  returns `critic_decisions: list[{contact_id, decision, critic_note}]`
  plus `critic_backend: 'ollama' | 'rules'`.
- Pattern inspired by Microsoft AutoGen 0.2's researcher/executor/critic
  trio, adapted to a single LangGraph node so it runs locally at
  **zero cost** via Ollama (`llama3.1:8b`).
- If `http://localhost:11434` is unreachable the node silently falls back
  to a deterministic rule-based decision so the graph never stalls.

## Test commands

From the `backend/` directory with the venv active:

```powershell
# Run the email_verification self-tests (6 cases, all rules).
.venv\Scripts\python.exe -m pipeline.email_verification

# Smoke-test imports for reconcile + critic.
.venv\Scripts\python.exe -c "from pipeline.reconcile import reconcile_contacts; from pipeline.critic import critic_node; print('imports OK')"
```

No new dependencies — `httpx` and `pydantic` are already pinned in
`requirements.txt`.

## B2B Enrichment (Fase 13)

`b2b_enrichment.py` re-implements Lorenzo's validated B2B Contact
Enrichment playbook (the one that found **292 verified emails out of 548
leads** at zero cost) as a callable async Python tool the LangGraph
orchestrator can invoke from an EXECUTE step.

### Inputs / outputs

```python
class EnrichmentTarget(BaseModel):
    company_name: str
    domain: str
    ceo_name: str | None = None
    org_nr: str | None = None

class EnrichmentResult(BaseModel):
    target: EnrichmentTarget
    discovered_contacts: list[ReconcileResult]
    verification_summary: dict   # total / valid / accepted / flagged / rejected / critic_backend
    audit_trail: list[str]
    critic_decisions: list[dict]
```

Two entry points:

```python
await enrich_b2b(target, *, max_queries=3, max_pages=4, run_critic=True)
await enrich_batch(targets, *, max_parallel=7, max_queries=3, max_pages=4)
```

### Constraints (vault-validated)

- **Max parallel companies = 7.** Anything higher trips per-IP rate limits
  on the dominant Swedish hosts.
- **Max 4 pages per company.** SERP results + canonical contact paths
  (`/kontakt`, `/om-oss`, `/team`, …) deduped together.
- Rejection rules live in `email_verification.py`:
  - reject generic locals (`info@`, `kontakt@`, `hej@`, `hello@`,
    `post@`, `mail@`, `support@`, `admin@`, …)
  - reject pattern-generated addresses with no textual evidence
  - reject `gmail.com` / `hotmail.com` / `yahoo.com` on a corporate domain
- Pipeline **never raises end-to-end**: SearXNG down → falls back to
  canonical contact-path probe; T2 throttled → returns partial results;
  Ollama down → Critic uses deterministic rule-based decisions.

### How the LangGraph agent uses it

Wrapper lives in `agent/tools.py`:

```python
async def b2b_enrichment_tool(state: AgentState) -> dict
```

It reads `state["plan_steps"]`, filters to steps where
`source == "b2b_enrichment"` (restricted to `approved_step_ids` when set),
flattens their `targets` / `target` / inline payload into
`list[EnrichmentTarget]`, calls `enrich_batch`, and writes back
`state["enrichment_results"]` as a list of `EnrichmentResult.model_dump()`
dicts. The EXECUTE node (wired in Fase 13b) will dispatch on
`step["source"]` to invoke this tool.

### CLI usage

Manual enrichment of a single company (debug only — does NOT write to
the DB):

```powershell
.venv\Scripts\python.exe -m scrapers.cli enrich \
    --domain savantmedia.se \
    --ceo "Lorenzo Dastoli" \
    --company "Savant Media AB" \
    --max-queries 3 \
    --max-pages 4
```

Output is the full `EnrichmentResult` as pretty-printed UTF-8 JSON.
