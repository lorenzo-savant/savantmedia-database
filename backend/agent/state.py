"""
Agent state definition for the Savantsdatabas LangGraph orchestrator.

The `AgentState` TypedDict is the single source of truth that flows between
LangGraph nodes. Each node receives the full state and returns a *partial*
update; LangGraph merges the partial dict back into the state.

Phase 6 scope (current): RECALL → PLAN → SAVE_PLAN → WAIT_APPROVAL.
Phase 8+ will add: EXECUTE, RECONCILE, CRITIC, MEMORY_UPDATE.
"""

from __future__ import annotations

from typing import Literal, TypedDict


# ─────────────────────────────────────────────────────────────────────────────
# Plan status literal
# ─────────────────────────────────────────────────────────────────────────────

PlanStatus = Literal["draft", "approved", "executing", "done", "cancelled"]
"""Lifecycle of a plan row in `public.plans`.

- draft:      emitted by the PLAN node, awaiting human approval
- approved:   user has confirmed (a subset of) steps via the cockpit UI
- executing:  EXECUTE node is currently running steps
- done:       all approved steps completed (success or partial)
- cancelled:  user rejected the plan or aborted mid-execution
"""


# ─────────────────────────────────────────────────────────────────────────────
# AgentState
# ─────────────────────────────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    """The state object that flows through the LangGraph.

    Marked `total=False` so each node can return a *partial* dict — LangGraph
    will merge it onto the running state. Required keys for an initial
    invocation are documented in `graph.run_plan_phase`.

    Fields
    ------
    user_prompt:
        Raw natural-language request from the operator
        (e.g. "trovami i CTO IT-konsulter Skåne").
    recall_context:
        RAG retrieval results. Shape:
            {
                "companies_match": list[dict],   # rows from public.companies
                "playbook_chunks": list[dict],   # chunks from knowledge_chunks
            }
    plan_steps:
        Ordered list of proposed steps. Each step:
            {
                "id":              str,        # short uid, e.g. "s1"
                "query":           str,        # the actual query/action text
                "source":          str,        # "bolagsverket" | "scb" | "vault" | "web" | ...
                "tier":            int,        # 0=free open data, 1=cheap, 2=paid api
                "expected_yield":  str,        # human description of expected output
                "rationale":       str,        # why this step, referencing recall_context
            }
    plan_status:
        Lifecycle marker — see `PlanStatus`.
    approved_step_ids:
        Subset of step ids that the user confirmed in the cockpit. Empty
        until the human-in-the-loop interrupt resumes.
    plan_id:
        UUID of the row in `public.plans` once persisted. None before save.
    messages:
        Append-only audit log of node executions. Each entry:
            {"node": str, "ts": str (ISO8601), "info": str}
    error:
        Last error string, if any node failed. Cleared on successful node exit.
    """

    user_prompt: str
    recall_context: dict
    plan_steps: list[dict]
    plan_status: PlanStatus
    approved_step_ids: list[str]
    plan_id: str | None
    messages: list[dict]
    error: str | None

    # ── Fase 12 EXECUTE chain (additive, total=False → backward compat) ─────
    # Per-step results from `agent.executor.execute_plan`. Shape:
    #     {
    #         "step_id":       str,
    #         "tier":          int,
    #         "status":        "done" | "failed" | "blocked",
    #         "result_count":  int,
    #         "result_data":   dict (ScrapeResult.dict() or EnrichmentResult.dict()),
    #         "scrape_job_id": str | None,
    #         "error":         str | None,
    #     }
    execution_results: list[dict]

    # Output of `agent.executor.reconcile_results`. Each entry is a
    # `pipeline.reconcile.ReconcileResult.model_dump()`.
    reconciled: list[dict]

    # Output of `pipeline.critic.critic_node`. Each entry:
    #     {"contact_id": str, "decision": "accept|flag_for_review|reject",
    #      "critic_note": str}
    critic_decisions: list[dict]

    # UUIDs of `public.scrape_jobs` rows created during execute_plan —
    # useful for live polling from the cockpit.
    scrape_job_ids: list[str]
