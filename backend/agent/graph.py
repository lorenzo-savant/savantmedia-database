"""
LangGraph compilation — Fase 12 (EXECUTE chain wired).

Graph topology
--------------

    START
      │
      ▼
    recall
      │
      ▼
    plan
      │
      ▼
    save_plan_to_db
      │
      ▼  (interrupt_before — human in the loop)
    wait_approval
      │
      ├── plan_status == "approved" ─▶ execute_plan
      │                                   │
      │                                   ▼
      │                                reconcile_results
      │                                   │
      │                                   ▼
      │                                critic
      │                                   │
      │                                   ▼
      │                                memory_update
      │                                   │
      │                                   ▼
      │                                  END
      │
      └── plan_status == "cancelled"  ─▶ END

Compiled with an in-memory `MemorySaver` checkpointer so a single
`thread_id` can be paused at `wait_approval` and later resumed with the
operator's approved step ids. Multi-process deployments need a
Postgres checkpointer — that's a Fase 13+ swap (see TODO in
`backend/agent/README.md`).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from pipeline.critic import critic_node

from .executor import execute_plan, reconcile_results
from .memory_node import memory_update_node
from .nodes import plan, recall, save_plan_to_db, wait_approval
from .state import AgentState

log = logging.getLogger("savantsdatabas.agent.graph")


# ─────────────────────────────────────────────────────────────────────────────
# Conditional routing — after wait_approval
# ─────────────────────────────────────────────────────────────────────────────


def _after_approval(state: AgentState) -> str:
    """Route based on the plan_status that the operator set.

    - "approved"   → execute the approved subset (EXECUTE → RECONCILE
                     → CRITIC → memory_update)
    - "cancelled"  → straight to END (no execution, no memory update)
    - anything else (defensive — e.g. operator only flipped status
      without re-invoking the graph) → END
    """
    status = state.get("plan_status")
    if status == "approved":
        return "execute_plan"
    if status == "cancelled":
        return "__end__"
    # Backward compat: Fase 11 routed `done` through memory_update directly.
    if status == "done":
        return "memory_update"
    return "__end__"


# ─────────────────────────────────────────────────────────────────────────────
# Build the graph
# ─────────────────────────────────────────────────────────────────────────────


def _build() -> Any:
    """Construct and compile the Fase 12 graph.

    Kept as a private function so tests can rebuild with a different
    checkpointer if needed.
    """
    builder = StateGraph(AgentState)

    builder.add_node("recall", recall)
    builder.add_node("plan", plan)
    builder.add_node("save_plan_to_db", save_plan_to_db)
    builder.add_node("wait_approval", wait_approval)
    builder.add_node("execute_plan", execute_plan)
    builder.add_node("reconcile_results", reconcile_results)
    builder.add_node("critic", critic_node)
    builder.add_node("memory_update", memory_update_node)

    builder.add_edge(START, "recall")
    builder.add_edge("recall", "plan")
    builder.add_edge("plan", "save_plan_to_db")
    builder.add_edge("save_plan_to_db", "wait_approval")

    # Branch on status after the human-in-the-loop interrupt resumes.
    builder.add_conditional_edges(
        "wait_approval",
        _after_approval,
        {
            "execute_plan": "execute_plan",
            "memory_update": "memory_update",
            "__end__": END,
        },
    )

    # EXECUTE → RECONCILE → CRITIC → memory_update → END
    builder.add_edge("execute_plan", "reconcile_results")
    builder.add_edge("reconcile_results", "critic")
    builder.add_edge("critic", "memory_update")
    builder.add_edge("memory_update", END)

    return builder.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["wait_approval"],
    )


# Module-level singleton — import this from FastAPI handlers.
compiled = _build()
"""The compiled LangGraph graph, ready to be invoked.

Halts *before* `wait_approval` so the cockpit UI can render the draft
plan and collect operator approval. Once the operator approves, the API
must call `compiled.ainvoke(None, config={"configurable":
{"thread_id": <id>}})` to resume the same thread — that pushes the
state through EXECUTE → RECONCILE → CRITIC → memory_update → END.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Convenience runner
# ─────────────────────────────────────────────────────────────────────────────


async def run_plan_phase(user_prompt: str, thread_id: str) -> AgentState:
    """Run the graph from START up to the `wait_approval` interrupt.

    Parameters
    ----------
    user_prompt:
        Raw operator request — populates `AgentState["user_prompt"]`.
    thread_id:
        Stable identifier for this conversation. Used by the checkpointer
        so a later call (with approved_step_ids) can resume the same run.

    Returns
    -------
    AgentState
        The state captured at the interrupt — contains `plan_steps`,
        `plan_id`, `recall_context`, and the audit `messages`.
    """
    config = {"configurable": {"thread_id": thread_id}}
    initial: AgentState = {
        "user_prompt": user_prompt,
        "recall_context": {},
        "plan_steps": [],
        "plan_status": "draft",
        "approved_step_ids": [],
        "plan_id": None,
        "messages": [],
        "error": None,
    }

    log.info("run_plan_phase: thread_id=%s prompt=%r", thread_id, user_prompt[:80])

    # `ainvoke` runs until the interrupt and returns the captured state.
    result = await compiled.ainvoke(initial, config=config)
    return result  # type: ignore[return-value]


async def resume_execute_phase(
    thread_id: str,
    *,
    approved_step_ids: list[str],
) -> AgentState:
    """Resume a paused thread through EXECUTE → RECONCILE → CRITIC → memory_update.

    Updates the checkpointed state so the conditional router fires the
    execute branch, then invokes the graph with ``None`` (the standard
    LangGraph "resume from interrupt" pattern).
    """
    config = {"configurable": {"thread_id": thread_id}}

    # Patch the checkpointed state so wait_approval sees the approval.
    compiled.update_state(
        config,
        {
            "approved_step_ids": list(approved_step_ids or []),
            "plan_status": "approved",
        },
    )

    log.info(
        "resume_execute_phase: thread_id=%s approved=%d step(s)",
        thread_id,
        len(approved_step_ids or []),
    )

    result = await compiled.ainvoke(None, config=config)
    return result  # type: ignore[return-value]
