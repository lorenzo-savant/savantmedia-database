# `backend/memory/` — Fase 11 Memory Writer

Implements the **memory writer** layer described in `docs/ARCHITECTURE.md` §7.
After every scraping run, the agent persists:

1. A human-readable Markdown note in the Obsidian vault under
   `Workflows/scraping-runs/YYYY-MM-DD-<slug>.md`.
2. Concrete lessons learned as `kind='lesson'` rows in
   `public.knowledge_chunks` (embedded with `nomic-embed-text` via Ollama).

It also seeds the canonical playbook files from the vault into
`knowledge_chunks` so RECALL can retrieve them via semantic search.

---

## The four memory layers (recap from §7)

```
┌─────────────────────────────────────────────────────────────┐
│  1. Postgres canonical (companies, contacts, sources)       │
│     → the truth about the data                              │
├─────────────────────────────────────────────────────────────┤
│  2. pgvector knowledge_chunks                               │
│     → snippets, query logs, lessons (embedded)              │
│     → answers "have I searched for something similar?"      │
├─────────────────────────────────────────────────────────────┤
│  3. Vault Obsidian (Workflows/scraping-runs/)               │
│     → human-readable playbooks written by the agent         │
│     → Lorenzo re-opens, refines, promotes to playbook       │
├─────────────────────────────────────────────────────────────┤
│  4. gbrain MCP (knowledge graph, project↔client↔contact)    │
│     → business-level, not scraping ops                      │
└─────────────────────────────────────────────────────────────┘
```

This module owns layers **2** and **3** only.

---

## Hard write boundary

The agent is allowed to write **only** under
`{VAULT_PATH}/Workflows/scraping-runs/`. Every other vault file is
**read-only**. Enforced in `vault_writer.py` via:

- a path-containment check (`_safe_target`) that refuses any resolved
  path which escapes `workflows_dir()`,
- no overwrite policy — collisions get `-2`, `-3`, … suffixes.

---

## Embeddings

`nomic-embed-text` via Ollama (`${OLLAMA_BASE_URL}/api/embeddings`,
default `http://localhost:11434`). 768-dim, matches the `vector(768)`
column. If Ollama is unreachable, `embed()` returns `None` and
`upsert_chunk` still inserts the row with `embedding=NULL` so the data
remains visible to the cockpit UI.

---

## Files

| File | Responsibility |
|---|---|
| `embeddings.py` | Async Ollama client (`embed`, `embed_batch`). |
| `vault_writer.py` | MD writer with hard write-boundary; `write_run_note(...)`. |
| `knowledge_chunks.py` | `upsert_chunk`, `ingest_vault_note`, `ingest_vault_seed`, `semantic_search`. |
| `__main__.py` | CLI: `seed`, `search "<query>"`, `test-write`. |

---

## CLI

```bash
cd backend

# Seed the canonical playbooks into pgvector (idempotent)
.venv/Scripts/python.exe -m memory seed

# Semantic search the knowledge base
.venv/Scripts/python.exe -m memory search "allabolag bransch sok"

# Verify vault is reachable and the write boundary works
.venv/Scripts/python.exe -m memory test-write
```

---

## Integration with the LangGraph

The node `agent.memory_node.memory_update_node` is the post-execute
terminal step. Wired in `agent/graph.py` as an **optional terminal node**
behind `plan_status == "done"`. Activated end-to-end in Fase 12+ once
EXECUTE / RECONCILE / CRITIC populate the state with execution results.
