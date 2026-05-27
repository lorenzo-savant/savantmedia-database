"""
LangGraph node implementations for the Phase 6 plan pipeline.

Each node is a pure function `(state: AgentState) -> dict` that returns a
*partial* state update. Side effects (DB writes, LLM calls) are isolated
inside the node body; nodes never mutate the input state in place.

Current status:
- `recall`             : placeholder — queries Supabase `companies` count,
                         returns mock playbook chunks.
                         TODO: integrate pgvector search + Obsidian vault.
- `plan`               : placeholder — emits a hardcoded 2-step plan.
                         TODO: integrate Groq Llama 3.3 70B JSON-mode call.
- `wait_approval`      : pass-through. The actual human-in-the-loop pause
                         is enforced by LangGraph's `interrupt_before`
                         configured in `graph.py`.
- `save_plan_to_db`    : functional — inserts/updates a row in
                         `public.plans` via the Supabase client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .state import AgentState

log = logging.getLogger("savantsdatabas.agent.nodes")


# Load backend/.env when this module is imported (idempotent — uvicorn already
# does this once, but standalone scripts via `python -c` need it too).
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
except Exception:  # noqa: BLE001
    pass


# ─────────────────────────────────────────────────────────────────────────────
# LLM-aware plan generator
# ─────────────────────────────────────────────────────────────────────────────


_PLAN_SYSTEM_PROMPT = """Du är en planerare för Savantsdatabas — en svensk företagsdatabas \
med en LangGraph-orchestrator. Användaren beskriver vilken data hen vill hitta. \
Du producerar ett genomförbart plan av 3-6 steg.

TIER-modell (lägre = billigare/säkrare, försök alltid lägre först):
- T0 = öppna data (bulk Bolagsverket/SCB CC-BY-4.0, gratis, ingen scraping)
- T1 = SearXNG meta-search (queries riktade till specifika sites)
- T2 = httpx + BeautifulSoup (statiska sidor: /kontakt, /om-oss, /team)
- T3 = crawl4ai med LLM-extraktion (SPA-sidor, strukturerad output)
- T4 = Playwright stealth (anti-bot tunga sidor med session-persistens)
- T5 = browser-use autonomous agent (komplexa multi-step-flöden)

VAULT-läxor (ignorera ej):
- allabolag.se/foretag/* är React SPA och BLOCKERAR scraping — använd ENDAST /bransch-sök listsidor (T4)
- E-postregler: reject info@/kontakt@/hej@/hello@/post@/mail@/support@/admin@; accept endast om hittad textuellt i public source
- "Liten" företag (≤49 anställda) = grundare ofta enda DM — max 1 verifierad kontakt per litet bolag
- Max 7 parallella enrichment-jobb (vault-läxa: 7 är praktisk gräns)

Output: STRIKT JSON enligt schemat:
{
  "steps": [
    {
      "query": "konkret söksträng eller URL",
      "source": "vault" | "supabase" | "bolagsverket_bulk" | "searxng" | "web_scrape" | "b2b_enrichment" | "browseruse",
      "tier": 0-5,
      "expected_yield": "vad förväntar vi oss att få",
      "rationale": "varför detta steg, varför just denna tier"
    }
  ]
}

INTE markdown, INTE preamble — bara giltigt JSON-objekt."""


def _build_plan_user_prompt(user_prompt: str, recall: dict[str, Any]) -> str:
    companies_match = recall.get("companies_match") or []
    playbook_chunks = recall.get("playbook_chunks") or []

    context_lines = [
        f"Användarens fråga: {user_prompt}",
        "",
        f"Recall-kontext: {len(companies_match)} företag matchar redan i DB, "
        f"{len(playbook_chunks)} playbook-chunkar relevanta.",
    ]
    if companies_match[:3]:
        context_lines.append(
            "Existerande matchningar (toppar): "
            + ", ".join(c.get("foretagsnamn", "?") for c in companies_match[:3])
        )
    return "\n".join(context_lines)


def _extract_json_block(text: str) -> str | None:
    """Best-effort JSON extraction even if LLM wraps in markdown fences."""
    if not text:
        return None
    # Try fenced first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Fallback: from first '{' to last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


async def _call_groq(prompt_user: str) -> dict[str, Any] | None:
    """Try Groq Llama 3.3 70B JSON-mode. Returns parsed JSON or None on failure."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt_user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        log.warning("plan: Groq call failed (%s) — falling back", exc)
        return None


async def _call_ollama(prompt_user: str) -> dict[str, Any] | None:
    """Try local Ollama Llama 3.1 8B in JSON mode. Returns parsed JSON or None."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL_REASONING", "llama3.1:8b")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt_user},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.3},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            block = _extract_json_block(content) or content
            return json.loads(block)
    except Exception as exc:  # noqa: BLE001
        log.warning("plan: Ollama call failed (%s) — falling back", exc)
        return None


async def _try_llm_plan(prompt_user: str) -> tuple[list[dict] | None, str]:
    """Try Groq first, then Ollama. Returns (steps_or_None, provider_used_label)."""
    for provider, caller in (("groq", _call_groq), ("ollama", _call_ollama)):
        parsed = await caller(prompt_user)
        if not parsed:
            continue
        steps = parsed.get("steps")
        if isinstance(steps, list) and steps:
            cleaned: list[dict] = []
            for idx, raw in enumerate(steps):
                if not isinstance(raw, dict):
                    continue
                cleaned.append(
                    {
                        "id": f"s{idx + 1}",
                        "query": str(raw.get("query", ""))[:500],
                        "source": str(raw.get("source", "vault"))[:50],
                        "tier": int(raw.get("tier", 0)) if str(raw.get("tier", 0)).lstrip("-").isdigit() else 0,
                        "expected_yield": str(raw.get("expected_yield", ""))[:500],
                        "rationale": str(raw.get("rationale", ""))[:500],
                    }
                )
            if cleaned:
                return cleaned, provider
    return None, "stub"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _audit(state: AgentState, node: str, info: str) -> list[dict]:
    """Return an updated `messages` list with a new audit entry appended.

    Always returns a new list — never mutates `state["messages"]`.
    """
    existing = list(state.get("messages") or [])
    existing.append({"node": node, "ts": _now_iso(), "info": info})
    return existing


# ─────────────────────────────────────────────────────────────────────────────
# Node: recall
# ─────────────────────────────────────────────────────────────────────────────


def recall(state: AgentState) -> dict[str, Any]:
    """Retrieve context for the upcoming plan.

    Phase 6 placeholder: only counts rows in `public.companies` to confirm
    DB connectivity, and returns a mock `playbook_chunks` payload.

    TODO (Phase 7):
        - pgvector similarity search against `knowledge_chunks` using
          nomic-embed-text embedding of `state["user_prompt"]`.
        - Read relevant playbook MD files from the Obsidian vault.
        - Issue a structured `companies` query when the prompt mentions
          an org_nr, foretagsnamn, kommun, etc.
    """
    prompt = state.get("user_prompt", "")
    log.info("recall: prompt=%r", prompt[:120])

    companies_match: list[dict] = []
    info = "recall stub: no LLM, no pgvector"

    # Best-effort connectivity check against Supabase — never blocks the graph.
    try:
        # Local import keeps `agent` importable even if `api.deps` is absent.
        from api.deps import get_supabase  # type: ignore

        sb = get_supabase()
        # `count='exact'` gives us a row count without pulling data.
        resp = sb.table("companies").select("id", count="exact").limit(1).execute()
        total = getattr(resp, "count", None)
        info = f"recall stub: companies_count={total}"
    except Exception as exc:  # noqa: BLE001 — placeholder is intentionally permissive
        log.warning("recall: Supabase probe skipped (%s)", exc)
        info = f"recall stub: supabase probe failed ({exc.__class__.__name__})"

    playbook_chunks = [
        {
            "kind": "playbook",
            "content": (
                "Mock playbook chunk — placeholder until pgvector search "
                "is wired up in Phase 7."
            ),
            "metadata": {"tier": 0, "source_file": "MOCK"},
        }
    ]

    return {
        "recall_context": {
            "companies_match": companies_match,
            "playbook_chunks": playbook_chunks,
        },
        "messages": _audit(state, "recall", info),
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: plan
# ─────────────────────────────────────────────────────────────────────────────


def plan(state: AgentState) -> dict[str, Any]:
    """Produce a structured list of proposed steps.

    Tries (in order): Groq Llama 3.3 70B JSON-mode → Ollama Llama 3.1 8B JSON
    → deterministic stub. Always returns a usable plan.
    """
    prompt = (state.get("user_prompt") or "").strip()
    log.info("plan: generating plan for prompt=%r", prompt[:120])

    snippet = " ".join(prompt.split())[:80] or "(empty prompt)"

    recall = state.get("recall_context") or {}
    companies_match = recall.get("companies_match") or []
    playbook_chunks = recall.get("playbook_chunks") or []

    # Try LLM providers
    llm_steps: list[dict] | None = None
    provider = "stub"
    if prompt:
        prompt_user = _build_plan_user_prompt(prompt, recall)
        try:
            llm_steps, provider = asyncio.get_event_loop().run_until_complete(
                _try_llm_plan(prompt_user)
            ) if not asyncio.get_event_loop().is_running() else (None, "stub")
        except RuntimeError:
            # Already inside a running loop (LangGraph async path) — schedule
            # via run_coroutine_threadsafe is too invasive; use nest_asyncio
            # pattern via asyncio.run on a new loop in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(lambda: asyncio.run(_try_llm_plan(prompt_user)))
                try:
                    llm_steps, provider = fut.result(timeout=180)
                except Exception as exc:  # noqa: BLE001
                    log.warning("plan: LLM thread failed (%s)", exc)
                    llm_steps, provider = None, "stub"

    if llm_steps:
        log.info("plan: LLM (%s) produced %d steps", provider, len(llm_steps))
        return {
            "plan_steps": llm_steps,
            "plan_status": "draft",
            "messages": _audit(
                state,
                "plan",
                f"LLM plan via {provider}: {len(llm_steps)} steps "
                f"(recall: {len(companies_match)} co, {len(playbook_chunks)} pb)",
            ),
            "error": None,
        }

    # Fallback hardcoded stub
    log.info("plan: LLM unavailable, falling back to stub")
    steps: list[dict] = [
        {
            "id": "s1",
            "query": (
                f"Recall: kontrollera om '{snippet}' redan finns i "
                f"public.companies innan vi spenderar API-budget."
            ),
            "source": "vault",
            "tier": 0,
            "expected_yield": (
                "Existerande org_nr + foretagsnamn som matchar prompten — "
                "dedupera innan T1+ scrape."
            ),
            "rationale": (
                f"Recall-context: {len(companies_match)} company match(es), "
                f"{len(playbook_chunks)} playbook chunk(s). "
                "Stub heuristic — Groq planner will replace this in Fase 6.5."
            ),
        },
        {
            "id": "s2",
            "query": (
                f"Bolagsverket Öppna data — bulk filter företag relaterade till "
                f"'{snippet}' (SNI/kommun-filter)."
            ),
            "source": "bolagsverket",
            "tier": 0,
            "expected_yield": (
                "Lista av org_nr + foretagsnamn + säte + SNI för matchande bolag."
            ),
            "rationale": (
                "Tier-0 open-data sweep (CC-BY 4.0 sedan feb 2025). "
                "Gratis källa, kör först. Stub placeholder."
            ),
        },
        {
            "id": "s3",
            "query": (
                f"SCB SNI lookup — berika varje hit från s2 med branschetikett "
                f"och storleksklass relevant för '{snippet}'."
            ),
            "source": "scb",
            "tier": 0,
            "expected_yield": "SNI-kod + branschetikett + storleksklass per bolag.",
            "rationale": (
                "Billig Tier-0 follow-up som gör cockpit-tabellen användbar. "
                "Stub placeholder."
            ),
        },
    ]

    return {
        "plan_steps": steps,
        "plan_status": "draft",
        "messages": _audit(
            state,
            "plan",
            (
                f"plan stub fallback ({provider}): {len(steps)} step(s) for "
                f"prompt={snippet!r} (recall: {len(companies_match)} co, "
                f"{len(playbook_chunks)} pb)"
            ),
        ),
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: save_plan_to_db
# ─────────────────────────────────────────────────────────────────────────────


def save_plan_to_db(state: AgentState) -> dict[str, Any]:
    """Persist the draft plan to `public.plans`.

    - If `state["plan_id"]` is set, UPDATE that row.
    - Otherwise, INSERT a new row and return the assigned UUID.

    On failure, the node records the error in state but does NOT raise —
    the graph stays runnable so the operator can inspect the partial state.
    """
    steps = state.get("plan_steps") or []
    status = state.get("plan_status") or "draft"
    plan_id = state.get("plan_id")

    payload = {
        "user_prompt": state.get("user_prompt", ""),
        "steps": steps,
        "status": status,
    }

    try:
        from api.deps import get_supabase  # type: ignore

        sb = get_supabase()
        if plan_id:
            resp = (
                sb.table("plans")
                .update(payload)
                .eq("id", plan_id)
                .execute()
            )
            log.info("save_plan_to_db: updated plan_id=%s", plan_id)
            info = f"updated plan {plan_id}"
            new_plan_id = plan_id
        else:
            resp = sb.table("plans").insert(payload).execute()
            row = (resp.data or [{}])[0]
            new_plan_id = row.get("id")
            log.info("save_plan_to_db: inserted plan_id=%s", new_plan_id)
            info = f"inserted plan {new_plan_id}"

        return {
            "plan_id": new_plan_id,
            "messages": _audit(state, "save_plan_to_db", info),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("save_plan_to_db: failed")
        return {
            "messages": _audit(
                state,
                "save_plan_to_db",
                f"FAILED: {exc.__class__.__name__}: {exc}",
            ),
            "error": f"save_plan_to_db: {exc}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Node: wait_approval
# ─────────────────────────────────────────────────────────────────────────────


def wait_approval(state: AgentState) -> dict[str, Any]:
    """Pure pass-through — the actual pause is handled by LangGraph.

    `graph.py` compiles the graph with `interrupt_before=["wait_approval"]`,
    so the graph halts *before* this node ever runs. The function body
    therefore only executes after the operator resumes via the cockpit UI
    (typically with `approved_step_ids` populated).
    """
    approved = state.get("approved_step_ids") or []
    log.info("wait_approval: resumed with %d approved step(s)", len(approved))
    return {
        "messages": _audit(
            state,
            "wait_approval",
            f"resumed with approved_step_ids={approved}",
        ),
    }
