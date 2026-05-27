# `agent/` — LangGraph orchestrator (Phase 6)

Scaffolding for the Savantsdatabas LangGraph agent. Implements the
**recall → plan → save → wait_approval** prefix of the flow defined in
`docs/ARCHITECTURE.md` §6. Execution / reconciliation / critic / memory
update arrive in later phases.

## What is implemented

| Node               | Status      | Notes                                                                 |
| ------------------ | ----------- | --------------------------------------------------------------------- |
| `recall`           | placeholder | Does a Supabase connectivity probe + returns a mock playbook chunk.   |
| `plan`             | placeholder | Returns a hardcoded 2-step example plan.                              |
| `save_plan_to_db`  | functional  | Inserts/updates a row in `public.plans` via the Supabase client.      |
| `wait_approval`    | functional  | Pass-through; pause is enforced by `interrupt_before` at compile time.|

The graph is compiled in `graph.py` with `MemorySaver` so a thread can be
paused and resumed with the operator's `approved_step_ids`.

## What is stubbed

- **Groq Llama 3.3 70B planner call** in `nodes.plan` — currently emits a
  hardcoded list. See the `TODO: integrate Groq` comment.
- **pgvector similarity search + Obsidian vault read** in `nodes.recall`
  — currently only probes `public.companies` for connectivity.
- **EXECUTE / RECONCILE / CRITIC / MEMORY_UPDATE** nodes — not yet wired.

## How to test

From `backend/`:

```python
# .venv/Scripts/python.exe -m asyncio
import asyncio
from agent.graph import run_plan_phase

state = asyncio.run(run_plan_phase(
    user_prompt="trovami i CTO IT-konsulter Skåne",
    thread_id="demo-001",
))

print("plan_id:", state.get("plan_id"))
print("status:", state.get("plan_status"))
for step in state.get("plan_steps", []):
    print(" -", step["id"], step["source"], step["query"])
for msg in state.get("messages", []):
    print("[audit]", msg["node"], "→", msg["info"])
```

Or, just to check the graph compiles:

```bash
.venv/Scripts/python.exe -c "from agent.graph import compiled; print(compiled.get_graph().nodes)"
```

## Next steps

| Phase  | Node                | What it adds                                                    |
| ------ | ------------------- | --------------------------------------------------------------- |
| 6.5    | `plan` (real)       | Groq Llama 3.3 70B JSON-mode call with pydantic validation.     |
| 7      | `recall` (real)     | pgvector search on `knowledge_chunks` + vault MD ingestion.     |
| 8      | `execute`           | Runs approved steps against Bolagsverket / SCB / scrapers.      |
| 9      | `reconcile`         | Dedupe + upsert into `companies` / `contacts`.                  |
| 10b    | `critic`            | LLM-as-judge pass over results; flags poor yields.              |
| 11     | `memory_update`     | Writes lessons back to `knowledge_chunks` for future RAG hits.  |
