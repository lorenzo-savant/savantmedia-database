"""
LangGraph EXECUTE chain — Fase 12 (`docs/ARCHITECTURE.md` §6 / §11).

The graph's earlier phases produce a draft plan and pause at
`wait_approval`. Once the operator approves a subset of steps and the
graph is resumed, this module is what actually runs them:

    1. `execute_plan(state)` — loops over `state["approved_step_ids"]`,
       calls `execute_step` for each, finalises the `scrape_jobs` row,
       and emits an audit `messages` entry.

    2. `execute_step(step)` — dispatches one plan step to the right
       scraper tier (T0..T5) or to the B2B enrichment pipeline. Always
       finalises the `scrape_jobs` row (status `done | failed | blocked`)
       even when the worker is unreachable.

    3. `reconcile_results(state)` — runs the validated reconcile rules
       over discovered contacts and exposes
       `state["reconciled"]: list[dict]` for downstream Critic.

Design constraints
------------------
- **Defensive end-to-end**: a worker failure (Ollama down, SearXNG
  offline, Playwright not installed) NEVER raises out of these
  functions. The scrape_jobs row gets `status='failed'` with an
  `error_message`, and execution moves to the next step.
- **Async** so it composes cleanly with the rest of the graph and the
  scrapers (which are all `async def`).
- **No state mutation** — every function returns a partial dict that
  LangGraph merges back onto the running state.
- **No tests trigger real scrapes**: the verification commands at the
  end of Fase 12 only import + compile-check.

Plan step shape (from `agent/nodes.plan`):

    {
        "id":              str,
        "query":           str,
        "source":          str,
        "tier":            int,
        "expected_yield":  str,
        "rationale":       str,
        # optional, per-step extras:
        "url":             str,
        "domain":          str,
        "company_name":    str,
        "ceo_name":        str,
        "org_nr":          str,
    }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .state import AgentState

log = logging.getLogger("savantsdatabas.agent.executor")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _audit(messages: list[dict] | None, node: str, info: str) -> list[dict]:
    """Append-only audit entry; never mutates input list."""
    existing = list(messages or [])
    existing.append({"node": node, "ts": _now_iso(), "info": info})
    return existing


def _get_supabase() -> Any | None:
    """Return a Supabase client, or None when not configured.

    Importing `api.deps` lazily keeps `agent.executor` importable in
    contexts where Supabase env vars aren't set (e.g. unit tests).
    """
    try:
        from api.deps import get_supabase  # type: ignore
        return get_supabase()
    except Exception as exc:  # noqa: BLE001 — degrade silently
        log.warning("executor: Supabase client unavailable (%s)", exc)
        return None


def _hostname(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def _step_url(step: dict) -> str:
    """Best-effort URL extraction from a plan step."""
    for key in ("url", "target_url", "query"):
        v = step.get(key)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v
    return ""


def _step_domain(step: dict) -> str:
    """Best-effort domain extraction from a plan step."""
    d = step.get("domain") or step.get("target_domain") or ""
    if isinstance(d, str) and d:
        return d.lower().lstrip("www.").strip("/")
    url = _step_url(step)
    return _hostname(url)


# ─────────────────────────────────────────────────────────────────────────────
# scrape_jobs row lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def _insert_scrape_job(
    sb: Any | None,
    *,
    plan_id: str | None,
    step: dict,
) -> str | None:
    """Insert a `scrape_jobs` row with status='running'. Returns its UUID.

    Returns None on failure — execution continues, but no DB linkage is
    recorded for this step.
    """
    if sb is None:
        return None
    try:
        payload = {
            "plan_id": plan_id,
            "query": str(step.get("query") or "")[:2000],
            "target_domain": _step_domain(step) or None,
            "tier_used": int(step.get("tier") or 0),
            "status": "running",
            "started_at": _now_iso(),
        }
        resp = sb.table("scrape_jobs").insert(payload).execute()
        row = (resp.data or [{}])[0]
        return row.get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("executor: insert scrape_jobs failed (%s)", exc)
        return None


def _finalize_scrape_job(
    sb: Any | None,
    job_id: str | None,
    *,
    status: str,
    result_count: int = 0,
    error_message: str | None = None,
    blocked_reason: str | None = None,
) -> None:
    """Mark the scrape_jobs row as terminal. Never raises."""
    if sb is None or not job_id:
        return
    try:
        payload: dict[str, Any] = {
            "status": status,
            "result_count": int(result_count or 0),
            "finished_at": _now_iso(),
        }
        if error_message:
            payload["error_message"] = error_message[:2000]
        if blocked_reason:
            payload["blocked_reason"] = blocked_reason[:2000]
        sb.table("scrape_jobs").update(payload).eq("id", job_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("executor: finalize scrape_jobs %s failed (%s)", job_id, exc)


def _insert_source_audit(
    sb: Any | None,
    *,
    scrape_result_dict: dict[str, Any] | None,
    field_name: str = "scrape.audit",
) -> None:
    """Best-effort audit insert into `public.sources`. Never raises."""
    if sb is None or not scrape_result_dict:
        return
    try:
        url = scrape_result_dict.get("url")
        tier = scrape_result_dict.get("tier")
        excerpt = (
            scrape_result_dict.get("raw_html_excerpt")
            or (scrape_result_dict.get("content_text") or "")[:500]
            or None
        )
        if not url:
            return
        payload = {
            "field_name": field_name,
            "source_url": url,
            "scraper_tier": tier,
            "raw_excerpt": excerpt,
        }
        sb.table("sources").insert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("executor: insert sources audit failed (%s)", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Per-tier dispatchers — each returns ``(result_dict, error_message)``
# ─────────────────────────────────────────────────────────────────────────────


async def _dispatch_t0(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 0 — Bolagsverket bulk import is offline-only.

    The agent cockpit can plan a T0 step for documentation, but the
    actual import is scheduled via `scripts/import_bolagsverket_bulk.py`
    on a separate cadence (large XML download, cron-driven).
    """
    msg = (
        "T0 bulk import runs offline via scripts.import_bolagsverket_bulk, "
        "not via agent dispatch"
    )
    result = {
        "tier": 0,
        "query": step.get("query"),
        "url": None,
        "error": msg,
        "metadata": {"manual_only": True},
    }
    return result, msg


async def _dispatch_t1(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 1 — SearXNG meta-search."""
    try:
        from scrapers.searxng import SearXNGClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"tier": 1, "error": f"import failed: {exc}"}, str(exc)

    client = SearXNGClient()
    query = str(step.get("query") or "").strip()
    if not query:
        return {"tier": 1, "error": "empty query"}, "empty query"

    try:
        results = await client.search(query, limit=10)
    except Exception as exc:  # noqa: BLE001
        return {"tier": 1, "error": f"search failed: {exc}"}, str(exc)

    if not results:
        return {"tier": 1, "query": query, "metadata": {"count": 0}}, None

    first = results[0]
    if not first.ok:
        return first.model_dump(mode="json"), first.error

    # Aggregate: keep the first as the canonical ScrapeResult, list the rest
    # in metadata.results for downstream consumers.
    aggregated = first.model_dump(mode="json")
    aggregated.setdefault("metadata", {})
    aggregated["metadata"]["results"] = [
        r.model_dump(mode="json") for r in results
    ]
    aggregated["metadata"]["count"] = len(results)
    return aggregated, None


async def _dispatch_t2(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 2 — httpx + trafilatura fetch."""
    try:
        from scrapers.httpbs import fetch_and_extract  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"tier": 2, "error": f"import failed: {exc}"}, str(exc)

    url = _step_url(step) or str(step.get("query") or "").strip()
    if not url.startswith(("http://", "https://")):
        msg = f"T2 needs a URL, got {url!r}"
        return {"tier": 2, "error": msg}, msg

    try:
        result = await fetch_and_extract(url)
    except Exception as exc:  # noqa: BLE001
        return {"tier": 2, "url": url, "error": f"fetch failed: {exc}"}, str(exc)

    return result.model_dump(mode="json"), result.error


async def _dispatch_t3(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 3 — crawl4ai with optional LLM extraction."""
    try:
        from scrapers.crawl4ai_worker import crawl_and_extract  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"tier": 3, "error": f"import failed: {exc}"}, str(exc)

    url = _step_url(step) or str(step.get("query") or "").strip()
    if not url.startswith(("http://", "https://")):
        msg = f"T3 needs a URL, got {url!r}"
        return {"tier": 3, "error": msg}, msg

    # Use the swedish_contact schema when this step is feeding the
    # B2B enrichment pipeline; otherwise let crawl4ai produce markdown only.
    schema = None
    if step.get("source") == "b2b_enrichment":
        try:
            from scrapers.schemas.swedish_contact import (  # type: ignore
                SWEDISH_CONTACT_EXTRACTION_SCHEMA,
            )
            schema = SWEDISH_CONTACT_EXTRACTION_SCHEMA
        except Exception as exc:  # noqa: BLE001
            log.warning("T3 schema import failed: %s", exc)

    try:
        result = await crawl_and_extract(
            url,
            extraction_schema=schema,
            llm_provider="ollama",
        )
    except Exception as exc:  # noqa: BLE001
        return {"tier": 3, "url": url, "error": f"crawl failed: {exc}"}, str(exc)

    return result.model_dump(mode="json"), result.error


async def _dispatch_t4(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 4 — Playwright stealth fetch."""
    try:
        from scrapers.playwright_t4 import stealth_fetch  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"tier": 4, "error": f"import failed: {exc}"}, str(exc)

    url = _step_url(step) or str(step.get("query") or "").strip()
    if not url.startswith(("http://", "https://")):
        msg = f"T4 needs a URL, got {url!r}"
        return {"tier": 4, "error": msg}, msg

    storage_key = _hostname(url) or "default"
    try:
        result = await stealth_fetch(url, storage_state_key=storage_key)
    except Exception as exc:  # noqa: BLE001
        return {"tier": 4, "url": url, "error": f"stealth_fetch failed: {exc}"}, str(exc)

    return result.model_dump(mode="json"), result.error


async def _dispatch_t5(step: dict) -> tuple[dict[str, Any], str | None]:
    """Tier 5 — browser-use autonomous agent."""
    try:
        from scrapers.browseruse_t5 import autonomous_navigate  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"tier": 5, "error": f"import failed: {exc}"}, str(exc)

    task = str(step.get("query") or "").strip()
    if not task:
        msg = "T5 needs a task (step.query)"
        return {"tier": 5, "error": msg}, msg

    start_url = step.get("url") or step.get("start_url")
    if isinstance(start_url, str) and not start_url.startswith(
        ("http://", "https://")
    ):
        start_url = None

    try:
        result = await autonomous_navigate(
            task=task,
            start_url=start_url if isinstance(start_url, str) else None,
            llm_provider="ollama",
        )
    except Exception as exc:  # noqa: BLE001
        return {"tier": 5, "error": f"browseruse failed: {exc}"}, str(exc)

    return result.model_dump(mode="json"), result.error


async def _dispatch_b2b_enrichment(
    step: dict,
) -> tuple[dict[str, Any], str | None]:
    """Special path — full B2B enrichment pipeline for one domain."""
    try:
        from pipeline.b2b_enrichment import (  # type: ignore
            EnrichmentTarget,
            enrich_b2b,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            {"tier": step.get("tier", 3), "error": f"import failed: {exc}"},
            str(exc),
        )

    # Build the target: step.query is interpreted as a domain (vault pattern).
    domain = (step.get("domain") or step.get("query") or "").strip()
    if not domain:
        msg = "b2b_enrichment needs a domain"
        return {"tier": step.get("tier", 3), "error": msg}, msg

    target = EnrichmentTarget(
        company_name=str(step.get("company_name") or domain),
        domain=domain,
        ceo_name=step.get("ceo_name"),
        org_nr=step.get("org_nr"),
    )

    try:
        enrichment_result = await enrich_b2b(target)
    except Exception as exc:  # noqa: BLE001 — enrich_b2b should never raise
        return (
            {"tier": step.get("tier", 3), "domain": domain, "error": str(exc)},
            str(exc),
        )

    dumped = enrichment_result.model_dump(mode="json")
    # Tier marker for downstream — vault default is T2/T3-ish for the pipeline.
    dumped.setdefault("tier", step.get("tier", 3))
    return dumped, None


# ─────────────────────────────────────────────────────────────────────────────
# execute_step
# ─────────────────────────────────────────────────────────────────────────────


async def execute_step(
    step: dict,
    *,
    supabase: Any | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Run one approved plan step end-to-end.

    Inserts a `scrape_jobs` row, dispatches to the right worker tier,
    updates the row to its terminal status, and (when applicable)
    inserts an audit row into `public.sources`.

    Parameters
    ----------
    step:
        A plan step dict (see module docstring for the shape).
    supabase:
        Optional pre-resolved Supabase client. If None, we resolve one
        lazily via `api.deps.get_supabase` — used by `execute_plan`.
    plan_id:
        Plan UUID to link the scrape_jobs row to.

    Returns
    -------
    dict
        ``{
            "step_id":        str,
            "tier":           int,
            "status":         "done" | "failed" | "blocked",
            "result_count":   int,
            "result_data":    dict,
            "scrape_job_id":  str | None,
            "error":          str | None,
        }``

    Never raises.
    """
    sb = supabase if supabase is not None else _get_supabase()
    step_id = str(step.get("id") or "")
    tier = int(step.get("tier") or 0)
    source = str(step.get("source") or "")

    # 1) Insert scrape_jobs row (best-effort)
    job_id = _insert_scrape_job(sb, plan_id=plan_id, step=step)

    # 2) Dispatch
    try:
        if source == "b2b_enrichment":
            result_dict, err = await _dispatch_b2b_enrichment(step)
        elif tier == 0:
            result_dict, err = await _dispatch_t0(step)
        elif tier == 1:
            result_dict, err = await _dispatch_t1(step)
        elif tier == 2:
            result_dict, err = await _dispatch_t2(step)
        elif tier == 3:
            result_dict, err = await _dispatch_t3(step)
        elif tier == 4:
            result_dict, err = await _dispatch_t4(step)
        elif tier == 5:
            result_dict, err = await _dispatch_t5(step)
        else:
            err = f"unknown tier={tier}"
            result_dict = {"tier": tier, "error": err}
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        log.exception("execute_step: dispatch raised unexpectedly")
        err = f"{exc.__class__.__name__}: {exc}"
        result_dict = {"tier": tier, "error": err}

    # 3) Compute status + counts
    if source == "b2b_enrichment" and not err:
        result_count = len(result_dict.get("discovered_contacts") or [])
        status = "done"
    elif err:
        # T0 is a documented "manual-only" path — surface as blocked, not failed.
        status = "blocked" if tier == 0 else "failed"
        result_count = 0
    else:
        meta = result_dict.get("metadata") or {}
        result_count = int(meta.get("count") or 1)
        status = "done"

    blocked = err if (status == "blocked") else None
    error_message = err if (status == "failed") else None

    # 4) Finalize scrape_jobs row
    _finalize_scrape_job(
        sb,
        job_id,
        status=status,
        result_count=result_count,
        error_message=error_message,
        blocked_reason=blocked,
    )

    # 5) Audit row in sources (only for successful URL-bearing scrapes)
    if status == "done" and result_dict.get("url") and source != "b2b_enrichment":
        _insert_source_audit(sb, scrape_result_dict=result_dict)

    return {
        "step_id": step_id,
        "tier": tier,
        "status": status,
        "result_count": result_count,
        "result_data": result_dict,
        "scrape_job_id": job_id,
        "error": err,
    }


# ─────────────────────────────────────────────────────────────────────────────
# execute_plan — graph node
# ─────────────────────────────────────────────────────────────────────────────


async def execute_plan(state: AgentState) -> dict[str, Any]:
    """Run all approved steps; collect per-step results.

    State writes:
      - ``execution_results``  : list[dict] (one per executed step)
      - ``scrape_job_ids``     : list[str]  (job UUIDs, where created)
      - ``plan_status``        : transitions to "executing" then "done"
      - ``messages``           : audit entries
    """
    plan_steps = list(state.get("plan_steps") or [])
    approved_set: set[str] = set(state.get("approved_step_ids") or [])
    plan_id = state.get("plan_id")
    sb = _get_supabase()

    # Filter steps: only those whose id is in approved_set. If the operator
    # somehow approved none, we treat the entire plan as approved (mirrors
    # the cockpit UI default).
    if approved_set:
        to_run = [s for s in plan_steps if str(s.get("id")) in approved_set]
    else:
        to_run = list(plan_steps)

    messages = _audit(
        state.get("messages"),
        "execute_plan",
        f"start: {len(to_run)} step(s) to run (plan_id={plan_id})",
    )

    if not to_run:
        return {
            "execution_results": [],
            "scrape_job_ids": [],
            "plan_status": "done",
            "messages": _audit(
                messages,
                "execute_plan",
                "no approved steps — skipping execute",
            ),
            "error": None,
        }

    execution_results: list[dict[str, Any]] = []
    job_ids: list[str] = []

    for step in to_run:
        try:
            result = await execute_step(step, supabase=sb, plan_id=plan_id)
        except Exception as exc:  # noqa: BLE001 — execute_step already swallows
            log.exception("execute_plan: execute_step raised unexpectedly")
            result = {
                "step_id": str(step.get("id") or ""),
                "tier": int(step.get("tier") or 0),
                "status": "failed",
                "result_count": 0,
                "result_data": {"error": str(exc)},
                "scrape_job_id": None,
                "error": str(exc),
            }
        execution_results.append(result)
        if result.get("scrape_job_id"):
            job_ids.append(str(result["scrape_job_id"]))
        messages = _audit(
            messages,
            "execute_plan",
            (
                f"step_id={result['step_id']} tier={result['tier']} "
                f"→ status={result['status']} count={result['result_count']}"
                + (f" error={result['error']}" if result.get("error") else "")
            ),
        )

    # Final plan_status: "done" if any non-failed result, else "done" still
    # (the operator can still inspect critic decisions). Use "executing" only
    # transiently in the audit.
    return {
        "execution_results": execution_results,
        "scrape_job_ids": job_ids,
        "plan_status": "done",
        "messages": _audit(
            messages,
            "execute_plan",
            (
                f"done: {sum(1 for r in execution_results if r['status'] == 'done')} ok / "
                f"{sum(1 for r in execution_results if r['status'] == 'failed')} failed / "
                f"{sum(1 for r in execution_results if r['status'] == 'blocked')} blocked"
            ),
        ),
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_results — graph node
# ─────────────────────────────────────────────────────────────────────────────


def _contacts_from_execution(
    execution_results: list[dict[str, Any]],
) -> tuple[list[Any], str | None]:
    """Walk execution_results, harvest Contact-shaped dicts for reconcile.

    Two sources:
      - b2b_enrichment results expose `discovered_contacts` (already
        reconciled — we pass them through unchanged).
      - other tiers may put raw contact dicts in
        ``result_data.metadata.contacts`` (T3 with LLM extraction).
    """
    # Local import — avoids forcing pydantic import at module load.
    try:
        from pipeline.reconcile import Contact  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [], f"pipeline.reconcile import failed: {exc}"

    candidates: list[Contact] = []
    company_domain: str | None = None

    for r in execution_results:
        data = r.get("result_data") or {}
        # b2b_enrichment — reconciled already, just collect domain hint.
        if isinstance(data, dict) and "discovered_contacts" in data:
            target = data.get("target") or {}
            if isinstance(target, dict) and target.get("domain"):
                company_domain = str(target["domain"])
            # Skip — already reconciled inside enrich_b2b.
            continue
        # T3 LLM extraction may surface contacts here.
        meta = (data.get("metadata") or {}) if isinstance(data, dict) else {}
        extracted = meta.get("extracted") if isinstance(meta, dict) else None
        if isinstance(extracted, dict):
            domain = extracted.get("domain")
            if domain and not company_domain:
                company_domain = str(domain)
            for c in extracted.get("kontakter") or []:
                if not isinstance(c, dict):
                    continue
                try:
                    candidates.append(
                        Contact(
                            namn=str(c.get("namn") or ""),
                            roll=str(c.get("roll") or ""),
                            email=str(c.get("email") or ""),
                            telefon=str(c.get("telefon") or ""),
                            linkedin_url=str(c.get("linkedin_url") or ""),
                            source_url=str(data.get("url") or ""),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("reconcile: bad contact shape %r (%s)", c, exc)

    return candidates, company_domain


async def reconcile_results(state: AgentState) -> dict[str, Any]:
    """Reconcile harvested contacts from `execution_results`.

    Writes ``state["reconciled"]: list[dict]`` so the Critic node can
    score each candidate. For b2b_enrichment results the
    `discovered_contacts` field already carries reconcile output; we
    pass those through unchanged.
    """
    execution_results = list(state.get("execution_results") or [])
    if not execution_results:
        return {
            "reconciled": [],
            "messages": _audit(
                state.get("messages"),
                "reconcile_results",
                "no execution_results to reconcile",
            ),
        }

    try:
        from pipeline.reconcile import reconcile_contacts  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {
            "reconciled": [],
            "messages": _audit(
                state.get("messages"),
                "reconcile_results",
                f"FAILED: import error: {exc}",
            ),
        }

    # 1) Pre-reconciled contacts from b2b_enrichment
    passthrough: list[dict[str, Any]] = []
    for r in execution_results:
        data = r.get("result_data") or {}
        if isinstance(data, dict):
            discovered = data.get("discovered_contacts") or []
            if isinstance(discovered, list):
                for d in discovered:
                    if isinstance(d, dict):
                        passthrough.append(d)

    # 2) Fresh reconcile for T3-extracted candidates
    candidates, company_domain = _contacts_from_execution(execution_results)
    fresh_dicts: list[dict[str, Any]] = []
    if candidates:
        try:
            fresh = reconcile_contacts(candidates, company_domain)
            fresh_dicts = [r.model_dump(mode="json") for r in fresh]
        except Exception as exc:  # noqa: BLE001
            log.exception("reconcile_contacts raised unexpectedly")
            return {
                "reconciled": passthrough,
                "messages": _audit(
                    state.get("messages"),
                    "reconcile_results",
                    f"partial: {len(passthrough)} passthrough, fresh FAILED: {exc}",
                ),
            }

    combined = passthrough + fresh_dicts
    return {
        "reconciled": combined,
        "messages": _audit(
            state.get("messages"),
            "reconcile_results",
            (
                f"reconciled {len(combined)} contact(s) "
                f"(passthrough={len(passthrough)}, fresh={len(fresh_dicts)})"
            ),
        ),
    }
