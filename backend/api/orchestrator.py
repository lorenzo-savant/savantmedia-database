"""
Orchestrator endpoints — wire the LangGraph agent (backend.agent) to FastAPI.

Endpoints (all gated by `verify_token`):

    POST /orchestrator/plan
        body: { user_prompt: str }
        Runs the LangGraph plan-phase (recall → plan → save_plan_to_db),
        halts at `wait_approval`, returns the persisted draft plan.

    GET /orchestrator/plans?limit=N
        Recent plans, shaped for the cockpit "Senaste planer" list.

    GET /orchestrator/plans/{plan_id}
        Full plan row (steps + approved_step_ids).

    POST /orchestrator/plans/{plan_id}/approve
        body: { approved_step_ids: list[str], thread_id?: str | None }
        Marks the plan as `approved` in Supabase. If `thread_id` is provided,
        we record it — Phase 8+ will actually resume the graph from the
        `wait_approval` interrupt and run EXECUTE.

Design notes:
- The LangGraph checkpointer used by `compiled` is in-memory (MemorySaver),
  so `thread_id` only survives within a single backend process. We therefore
  return `thread_id` to the client on /plan and accept it back on /approve,
  rather than persisting it in the DB. Phase 8 will swap to a persistent
  checkpointer.
- All endpoints degrade gracefully when external services (Ollama, Groq,
  SearXNG) are down because the current nodes are stubbed and don't call them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agent.graph import compiled, resume_execute_phase, run_plan_phase
from api.deps import get_supabase, verify_token

log = logging.getLogger("savantsdatabas.api.orchestrator")

router = APIRouter(
    prefix="/orchestrator",
    tags=["orchestrator"],
    dependencies=[Depends(verify_token)],
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────


class PlanStepModel(BaseModel):
    """Mirrors AgentState.plan_steps entries (see backend/agent/state.py)."""

    id: str
    query: str
    source: str
    tier: int
    expected_yield: str
    rationale: str


class PlanRequest(BaseModel):
    user_prompt: str = Field(..., min_length=1, max_length=4000)


class PlanResponse(BaseModel):
    plan_id: str
    thread_id: str
    steps: list[PlanStepModel]
    status: str = "draft"
    user_prompt: str
    created_at: Optional[str] = None


class PlanRowResponse(BaseModel):
    """Compact row used by the cockpit "Senaste planer" list."""

    id: str
    user_prompt: str
    status: str
    created_at: str
    step_count: int
    approved_count: int


class PlanFullResponse(BaseModel):
    id: str
    user_prompt: str
    status: str
    created_at: str
    approved_at: Optional[str] = None
    steps: list[PlanStepModel]
    approved_step_ids: list[str] = Field(default_factory=list)
    # Fase 12: set to True by /approve when the LangGraph resume task
    # has been scheduled in the background.
    executing: bool = False


class ApproveRequest(BaseModel):
    approved_step_ids: list[str] = Field(default_factory=list)
    thread_id: Optional[str] = None


class ScrapeJobRow(BaseModel):
    """Single `scrape_jobs` row, projected for the cockpit live view."""

    id: str
    plan_id: Optional[str] = None
    query: Optional[str] = None
    target_domain: Optional[str] = None
    tier_used: Optional[int] = None
    status: str
    result_count: Optional[int] = None
    blocked_reason: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None


class PlanExecutionResponse(BaseModel):
    """Live execution view of a plan: scrape_jobs + optional state snapshot."""

    plan_id: str
    jobs: list[ScrapeJobRow] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    state: Optional[dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _coerce_steps(raw: Any) -> list[PlanStepModel]:
    """Best-effort coercion of jsonb `steps` payload into PlanStepModel list."""
    if not isinstance(raw, list):
        return []
    out: list[PlanStepModel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                PlanStepModel(
                    id=str(item.get("id", "")),
                    query=str(item.get("query", "")),
                    source=str(item.get("source", "")),
                    tier=int(item.get("tier", 0) or 0),
                    expected_yield=str(item.get("expected_yield", "")),
                    rationale=str(item.get("rationale", "")),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("step coercion skipped (%s): %r", exc, item)
    return out


def _coerce_approved(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if isinstance(x, (str, int))]


def _row_to_full(row: dict[str, Any]) -> PlanFullResponse:
    return PlanFullResponse(
        id=str(row["id"]),
        user_prompt=str(row.get("user_prompt") or ""),
        status=str(row.get("status") or "draft"),
        created_at=str(row.get("created_at") or ""),
        approved_at=row.get("approved_at"),
        steps=_coerce_steps(row.get("steps")),
        approved_step_ids=_coerce_approved(row.get("approved_steps")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /orchestrator/plan
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/plan", response_model=PlanResponse)
async def create_plan(req: PlanRequest) -> PlanResponse:
    """Run the LangGraph plan-phase and return the persisted draft plan.

    Halts at `wait_approval` (interrupt). The returned `thread_id` must be
    passed back to /approve in Phase 8+ to resume execution.
    """
    thread_id = str(uuid.uuid4())
    log.info("POST /orchestrator/plan: thread_id=%s prompt=%r", thread_id, req.user_prompt[:80])

    try:
        state = await run_plan_phase(req.user_prompt, thread_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("run_plan_phase failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plan generation failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    plan_id = state.get("plan_id")
    if not plan_id:
        # save_plan_to_db logged the error in state["error"]; surface it.
        err = state.get("error") or "save_plan_to_db did not return a plan_id"
        log.error("create_plan: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"plan persistence failed: {err}",
        )

    steps = _coerce_steps(state.get("plan_steps"))
    return PlanResponse(
        plan_id=str(plan_id),
        thread_id=thread_id,
        steps=steps,
        status=str(state.get("plan_status") or "draft"),
        user_prompt=req.user_prompt,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /orchestrator/plans
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/plans", response_model=list[PlanRowResponse])
def list_plans(limit: int = 10) -> list[PlanRowResponse]:
    """Return the most recent plans, ordered by created_at desc."""
    capped = max(1, min(int(limit or 10), 100))
    sb = get_supabase()
    try:
        resp = (
            sb.table("plans")
            .select("id, user_prompt, status, created_at, steps, approved_steps")
            .order("created_at", desc=True)
            .limit(capped)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("list_plans: supabase query failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db query failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    rows = resp.data or []
    out: list[PlanRowResponse] = []
    for r in rows:
        steps_raw = r.get("steps") or []
        approved_raw = r.get("approved_steps") or []
        step_count = len(steps_raw) if isinstance(steps_raw, list) else 0
        approved_count = len(approved_raw) if isinstance(approved_raw, list) else 0
        out.append(
            PlanRowResponse(
                id=str(r["id"]),
                user_prompt=str(r.get("user_prompt") or ""),
                status=str(r.get("status") or "draft"),
                created_at=str(r.get("created_at") or ""),
                step_count=step_count,
                approved_count=approved_count,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /orchestrator/plans/{plan_id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}", response_model=PlanFullResponse)
def get_plan(plan_id: str) -> PlanFullResponse:
    sb = get_supabase()
    try:
        resp = (
            sb.table("plans")
            .select("*")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("get_plan: supabase query failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db query failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    rows = resp.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plan {plan_id} not found",
        )
    return _row_to_full(rows[0])


# ─────────────────────────────────────────────────────────────────────────────
# POST /orchestrator/plans/{plan_id}/approve
# ─────────────────────────────────────────────────────────────────────────────


# In-memory: which (plan_id|thread_id) pairs already have a background
# execute task in flight. Prevents accidental double-execution if the UI
# double-clicks Approve. Cleared on process restart — same lifetime as
# MemorySaver, which is acceptable for the current single-process setup.
_EXECUTING_THREADS: set[str] = set()


async def _execute_in_background(
    *,
    plan_id: str,
    thread_id: str,
    approved_step_ids: list[str],
) -> None:
    """Background task — resume the LangGraph thread through EXECUTE chain.

    Never raises: any failure is logged so the FastAPI process stays up.
    Updates `public.plans.status` to `done` (or `cancelled` on hard fail)
    once the chain terminates.
    """
    log.info(
        "background execute: plan_id=%s thread_id=%s approved=%d",
        plan_id,
        thread_id,
        len(approved_step_ids),
    )
    sb = get_supabase()

    # Mark plan as executing right away — UI polling will see this.
    try:
        sb.table("plans").update({"status": "executing"}).eq("id", plan_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("background execute: mark executing failed (%s)", exc)

    final_state: dict[str, Any] | None = None
    try:
        final_state = await resume_execute_phase(
            thread_id, approved_step_ids=approved_step_ids
        )  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        log.exception("background execute: resume_execute_phase failed")
        try:
            sb.table("plans").update(
                {
                    "status": "cancelled",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", plan_id).execute()
        except Exception as exc2:  # noqa: BLE001
            log.warning("background execute: cancel-mark failed (%s)", exc2)
    else:
        # Resume succeeded; record terminal status.
        try:
            sb.table("plans").update(
                {
                    "status": "done",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", plan_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("background execute: mark done failed (%s)", exc)

    log.info(
        "background execute finished: plan_id=%s thread_id=%s ok=%s",
        plan_id,
        thread_id,
        final_state is not None,
    )
    _EXECUTING_THREADS.discard(thread_id)


@router.post("/plans/{plan_id}/approve", response_model=PlanFullResponse)
async def approve_plan(
    plan_id: str,
    req: ApproveRequest,
    background_tasks: BackgroundTasks,
) -> PlanFullResponse:
    """Approve a subset of steps and (optionally) kick off the EXECUTE chain.

    Flow:
      1. Persist approval in `public.plans` (status → "approved").
      2. If `thread_id` is provided AND a matching LangGraph checkpoint
         exists, schedule a background task that resumes the graph,
         running EXECUTE → RECONCILE → CRITIC → memory_update. The HTTP
         response returns immediately with ``executing=True``.
      3. If no `thread_id` (or checkpoint missing), return immediately
         with ``executing=False`` — the UI gets the persisted plan but
         no async execution starts.
    """
    sb = get_supabase()
    cleaned = sorted({s for s in req.approved_step_ids if isinstance(s, str) and s})

    # 1) Persist approval in Supabase
    update_payload = {
        "approved_steps": cleaned,
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = (
            sb.table("plans")
            .update(update_payload)
            .eq("id", plan_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("approve_plan: supabase update failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db update failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    rows = resp.data or []
    if not rows:
        # Either no row matched or supabase didn't return updated rows.
        try:
            check = (
                sb.table("plans")
                .select("id")
                .eq("id", plan_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("approve_plan: re-check failed: %s", exc)
            check = None
        if not check or not (check.data or []):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"plan {plan_id} not found",
            )

    # 2) Schedule execute in background if we have a live thread_id
    executing = False
    if req.thread_id:
        try:
            cfg = {"configurable": {"thread_id": req.thread_id}}
            snapshot = compiled.get_state(cfg)
            has_checkpoint = bool(
                snapshot and getattr(snapshot, "values", None)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("approve_plan: checkpoint probe failed (%s)", exc)
            has_checkpoint = False

        if has_checkpoint and req.thread_id not in _EXECUTING_THREADS:
            _EXECUTING_THREADS.add(req.thread_id)
            background_tasks.add_task(
                _execute_in_background,
                plan_id=plan_id,
                thread_id=req.thread_id,
                approved_step_ids=cleaned,
            )
            executing = True
            log.info(
                "approve_plan: scheduled background execute (plan_id=%s, thread_id=%s)",
                plan_id,
                req.thread_id,
            )
        elif not has_checkpoint:
            log.info(
                "approve_plan: thread_id=%s has no checkpoint (process restart?) — "
                "skipping execute",
                req.thread_id,
            )

    # 3) Re-fetch full row and return
    try:
        full = (
            sb.table("plans")
            .select("*")
            .eq("id", plan_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("approve_plan: refetch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db refetch failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    full_rows = full.data or []
    if not full_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plan {plan_id} disappeared after update",
        )
    out = _row_to_full(full_rows[0])
    out.executing = executing
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /orchestrator/plans/{plan_id}/execution
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/plans/{plan_id}/execution", response_model=PlanExecutionResponse
)
def get_plan_execution(
    plan_id: str, thread_id: Optional[str] = None
) -> PlanExecutionResponse:
    """Live view of scrape_jobs for a given plan + optional state snapshot.

    The cockpit polls this every ~3s while any job is still pending or
    running. The ``state`` field is populated only when ``thread_id`` is
    supplied AND the in-memory checkpoint still exists — useful for
    inspecting `execution_results` / `reconciled` / `critic_decisions`
    without an extra DB query.
    """
    sb = get_supabase()
    try:
        resp = (
            sb.table("scrape_jobs")
            .select("*")
            .eq("plan_id", plan_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("get_plan_execution: scrape_jobs query failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db query failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    raw_jobs = resp.data or []
    jobs: list[ScrapeJobRow] = []
    counts: dict[str, int] = {
        "pending": 0,
        "running": 0,
        "done": 0,
        "blocked": 0,
        "failed": 0,
    }
    for r in raw_jobs:
        try:
            row = ScrapeJobRow(
                id=str(r["id"]),
                plan_id=r.get("plan_id"),
                query=r.get("query"),
                target_domain=r.get("target_domain"),
                tier_used=r.get("tier_used"),
                status=str(r.get("status") or "pending"),
                result_count=r.get("result_count"),
                blocked_reason=r.get("blocked_reason"),
                error_message=r.get("error_message"),
                started_at=r.get("started_at"),
                finished_at=r.get("finished_at"),
                created_at=r.get("created_at"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("get_plan_execution: skip row %r (%s)", r.get("id"), exc)
            continue
        jobs.append(row)
        counts[row.status] = counts.get(row.status, 0) + 1

    state_snapshot: Optional[dict[str, Any]] = None
    if thread_id:
        try:
            cfg = {"configurable": {"thread_id": thread_id}}
            snap = compiled.get_state(cfg)
            if snap is not None and getattr(snap, "values", None):
                # Strip noisy / large fields before returning.
                values = dict(snap.values)
                values.pop("recall_context", None)
                state_snapshot = values
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "get_plan_execution: state snapshot for %s failed (%s)",
                thread_id,
                exc,
            )

    return PlanExecutionResponse(
        plan_id=plan_id,
        jobs=jobs,
        counts=counts,
        state=state_snapshot,
    )
