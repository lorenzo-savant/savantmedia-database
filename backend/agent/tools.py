"""
Agent-callable tools for the Savantsdatabas LangGraph orchestrator.

A *tool* in this module is a LangGraph-node-shaped async callable:
``async def <tool>(state: AgentState) -> dict`` returning a *partial*
state update that the graph merges.

Phase 13 ships exactly one tool — `b2b_enrichment_tool` — which wraps the
validated B2B Contact Enrichment pipeline from
`pipeline.b2b_enrichment` so the EXECUTE node can call it whenever an
approved plan step has ``source == "b2b_enrichment"``.

The graph wiring itself lives in `agent/graph.py`; this module only
provides the tool body so EXECUTE can import it without circular deps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pipeline.b2b_enrichment import (
    EnrichmentTarget,
    enrich_batch,
)

from .state import AgentState

log = logging.getLogger("savantsdatabas.agent.tools")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 — matches `nodes._now_iso`."""
    return datetime.now(timezone.utc).isoformat()


def _audit_append(state: AgentState, node: str, info: str) -> list[dict]:
    """Return an extended `messages` list (never mutates state in place)."""
    existing = list(state.get("messages") or [])
    existing.append({"node": node, "ts": _now_iso(), "info": info})
    return existing


def _targets_from_step(step: dict[str, Any]) -> list[EnrichmentTarget]:
    """Extract one or more EnrichmentTargets from a single plan step.

    A plan step with ``source == "b2b_enrichment"`` is expected to carry
    its targets in either:
        - ``step["targets"]`` — list of dicts, OR
        - ``step["target"]``  — single dict, OR
        - inline keys (``company_name``/``domain``/``ceo_name``/``org_nr``)
          on the step itself.

    Anything unparseable is skipped silently — the audit trail in the
    returned EnrichmentResult will tell the operator why nothing happened.
    """
    raw_targets: list[dict[str, Any]] = []
    if isinstance(step.get("targets"), list):
        raw_targets = [t for t in step["targets"] if isinstance(t, dict)]
    elif isinstance(step.get("target"), dict):
        raw_targets = [step["target"]]
    elif step.get("domain"):
        raw_targets = [
            {
                "company_name": step.get("company_name", "")
                or step.get("query", ""),
                "domain": step.get("domain", ""),
                "ceo_name": step.get("ceo_name"),
                "org_nr": step.get("org_nr"),
            }
        ]

    out: list[EnrichmentTarget] = []
    for t in raw_targets:
        try:
            out.append(EnrichmentTarget(**t))
        except Exception as exc:  # noqa: BLE001 — bad target shape, skip & log
            log.warning("skipping malformed b2b_enrichment target %r: %s", t, exc)
    return out


# ─── Tool: b2b_enrichment_tool ───────────────────────────────────────────────


async def b2b_enrichment_tool(state: AgentState) -> dict[str, Any]:
    """LangGraph-node-shaped wrapper around `pipeline.enrich_batch`.

    Reads `state["plan_steps"]` (filtered to approved ids when
    `approved_step_ids` is set), picks the steps whose ``source`` is
    ``"b2b_enrichment"``, collects every EnrichmentTarget they carry, and
    runs them through `enrich_batch` with vault-default concurrency.

    The result is written back as ``state["enrichment_results"]``: a list
    of plain dicts (one per target, NOT one per step) shaped like
    `EnrichmentResult.model_dump()`.

    Failure modes:
        - No matching steps        → empty list, audit message, no error.
        - No targets in matching   → empty list, audit message, no error.
        - `enrich_batch` raises    → caught, error string written to state.

    The function is async and side-effect-free except for the
    HTTP/Ollama calls made by the underlying pipeline.
    """
    plan_steps = list(state.get("plan_steps") or [])
    approved_ids: set[str] = set(state.get("approved_step_ids") or [])

    # Filter: only b2b_enrichment-sourced steps. If the operator approved
    # a subset, restrict to that subset; otherwise consider all draft steps.
    relevant: list[dict[str, Any]] = []
    for step in plan_steps:
        if not isinstance(step, dict):
            continue
        if step.get("source") != "b2b_enrichment":
            continue
        if approved_ids and step.get("id") not in approved_ids:
            continue
        relevant.append(step)

    if not relevant:
        info = "no b2b_enrichment step found in plan_steps"
        log.info("b2b_enrichment_tool: %s", info)
        return {
            "enrichment_results": [],
            "messages": _audit_append(state, "b2b_enrichment_tool", info),
        }

    # Flatten targets across steps, preserving order. Duplicates by domain
    # are kept — the caller can dedup downstream if it wants to.
    targets: list[EnrichmentTarget] = []
    for step in relevant:
        targets.extend(_targets_from_step(step))

    if not targets:
        info = (
            f"{len(relevant)} b2b_enrichment step(s) but no parseable targets"
        )
        log.info("b2b_enrichment_tool: %s", info)
        return {
            "enrichment_results": [],
            "messages": _audit_append(state, "b2b_enrichment_tool", info),
        }

    log.info(
        "b2b_enrichment_tool: running enrich_batch over %d target(s) "
        "from %d step(s)",
        len(targets),
        len(relevant),
    )

    try:
        results = await enrich_batch(targets)
    except Exception as exc:  # noqa: BLE001 — pipeline already swallows; safety net
        log.exception("enrich_batch raised unexpectedly")
        return {
            "enrichment_results": [],
            "messages": _audit_append(
                state,
                "b2b_enrichment_tool",
                f"FAILED: {exc.__class__.__name__}: {exc}",
            ),
            "error": f"b2b_enrichment_tool: {exc}",
        }

    serialized = [r.model_dump() for r in results]
    total_contacts = sum(
        len(r.discovered_contacts) for r in results
    )
    info = (
        f"enriched {len(results)} target(s); "
        f"discovered {total_contacts} contact(s) total"
    )
    return {
        "enrichment_results": serialized,
        "messages": _audit_append(state, "b2b_enrichment_tool", info),
        "error": None,
    }
