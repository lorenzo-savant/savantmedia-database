"""
LangGraph node — `memory_update` (Fase 11, `docs/ARCHITECTURE.md` §6/§7).

Runs after the post-execute pipeline (RECONCILE → CRITIC) and persists:
  1. A human-readable MD note in the Obsidian vault under
     `Workflows/scraping-runs/YYYY-MM-DD-<slug>.md`.
  2. Each concrete lesson learned as a `kind='lesson'` row in
     `public.knowledge_chunks` (pgvector-embedded for future recall).

Design constraints
------------------
- **Async** (matches the rest of the graph).
- **Never raises**: vault unreachable, Supabase down, Ollama offline →
  log + degrade. The post-execute path of the graph must always
  terminate cleanly so the operator sees a final state.
- **Non-destructive vault writes**: handled inside
  `memory.vault_writer.write_run_note` (numeric suffix on collision).

Wired in `agent/graph.py` as an optional terminal node; activated when
EXECUTE is plugged in (Fase 12+).
"""

from __future__ import annotations

import logging
from typing import Any

from memory.knowledge_chunks import upsert_chunk
from memory.vault_writer import write_run_note

from .state import AgentState

log = logging.getLogger("savantsdatabas.agent.memory_node")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _audit(state: AgentState, info: str) -> list[dict]:
    existing = list(state.get("messages") or [])
    existing.append({"node": "memory_update", "ts": _now_iso(), "info": info})
    return existing


def _derive_lessons(state: AgentState) -> list[str]:
    """Heuristic: compose lesson lines from audit messages + error state.

    Until the CRITIC node emits structured lessons (Fase 10b), this
    layer ingests:
      - the final `error` if present
      - every audit `info` that looks like a verdict ("FAILED:",
        "→ suggest", "blocked", etc.)
    """
    lessons: list[str] = []
    err = state.get("error")
    if err:
        lessons.append(f"Run terminated with error: {err}")

    msgs = state.get("messages") or []
    interesting_markers = ("FAILED", "→", "blocked", "captcha", "rate_limit", "lesson:")
    for m in msgs:
        info = (m.get("info") or "").strip()
        if not info:
            continue
        if any(marker.lower() in info.lower() for marker in interesting_markers):
            node = m.get("node", "?")
            lessons.append(f"[{node}] {info}")
    return lessons


def _summarise_results(state: AgentState) -> dict[str, Any]:
    """Pull whatever execution evidence we have onto a flat dict for the MD note."""
    out: dict[str, Any] = {
        "plan_status": state.get("plan_status"),
        "plan_id": state.get("plan_id"),
        "approved_steps": len(state.get("approved_step_ids") or []),
        "total_steps": len(state.get("plan_steps") or []),
    }
    recall = state.get("recall_context") or {}
    if recall:
        out["recall_companies"] = len(recall.get("companies_match") or [])
        out["recall_playbook_chunks"] = len(recall.get("playbook_chunks") or [])
    if state.get("error"):
        out["error"] = state["error"]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Node
# ─────────────────────────────────────────────────────────────────────────────


async def memory_update_node(state: AgentState) -> dict[str, Any]:
    """Write run note to vault + upsert lessons to pgvector. Never raises."""
    run_id = str(state.get("plan_id") or "no-plan-id")
    user_prompt = state.get("user_prompt") or ""
    plan_steps = state.get("plan_steps") or []
    lessons = _derive_lessons(state)
    results = _summarise_results(state)

    vault_path_str: str | None = None
    vault_filename: str | None = None
    chunk_ids: list[str] = []
    audit_bits: list[str] = []

    # 1. Vault write
    try:
        written = write_run_note(
            run_id=run_id,
            user_prompt=user_prompt,
            plan_steps=plan_steps,
            results=results,
            lessons=lessons,
        )
        vault_path_str = str(written)
        vault_filename = written.name
        audit_bits.append(f"vault={vault_filename}")
    except Exception as exc:  # noqa: BLE001 — degrade, never break the graph
        log.warning("memory_update: vault write failed: %s", exc)
        audit_bits.append(f"vault=FAILED({exc.__class__.__name__})")

    # 2. Upsert lessons (one chunk per lesson — small, focused, embeddable)
    if lessons:
        lesson_vault_ref = (
            f"Workflows/scraping-runs/{vault_filename}" if vault_filename else None
        )
        for line in lessons:
            try:
                cid = await upsert_chunk(
                    kind="lesson",
                    content=line,
                    metadata={
                        "run_id": run_id,
                        "plan_status": state.get("plan_status"),
                        "user_prompt": user_prompt[:200],
                    },
                    vault_path=lesson_vault_ref,
                )
                if cid:
                    chunk_ids.append(cid)
            except Exception as exc:  # noqa: BLE001
                log.warning("memory_update: lesson upsert failed: %s", exc)
        audit_bits.append(f"lessons={len(lessons)} ids={len(chunk_ids)}")
    else:
        audit_bits.append("lessons=0")

    info = "memory_update: " + ", ".join(audit_bits)
    log.info(info)

    return {
        "memory_vault_path": vault_path_str,
        "memory_chunk_ids": chunk_ids,
        "messages": _audit(state, info),
    }
