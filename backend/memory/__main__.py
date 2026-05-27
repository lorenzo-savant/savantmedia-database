"""
`python -m memory` CLI — Fase 11 ops.

Subcommands
-----------
- `seed`              Run `ingest_vault_seed()` (idempotent).
- `search "<query>"`  Semantic search; prints top 5 chunks.
- `test-write`        Write a synthetic run note to the vault under
                      `Workflows/scraping-runs/` to verify access.

Loads `backend/.env` from disk before anything else so
`SUPABASE_URL` / `SUPABASE_SECRET_KEY` / `OLLAMA_BASE_URL` / `VAULT_PATH`
are available regardless of the cwd.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load env BEFORE importing modules that read it.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _usage() -> int:
    print(
        "Usage:\n"
        "  python -m memory seed\n"
        '  python -m memory search "<query>"\n'
        "  python -m memory test-write\n",
        file=sys.stderr,
    )
    return 2


async def _seed() -> int:
    from memory.knowledge_chunks import ingest_vault_seed

    out = await ingest_vault_seed()
    print(json.dumps({k: len(v) for k, v in out.items()}, indent=2))
    return 0


async def _search(query: str) -> int:
    from memory.knowledge_chunks import semantic_search

    rows = await semantic_search(query, limit=5)
    if not rows:
        print("(no results — Ollama/Supabase unreachable, or empty index)")
        return 0
    for i, r in enumerate(rows, start=1):
        sim = r.get("similarity")
        sim_s = f"{sim:.3f}" if isinstance(sim, (int, float)) else "n/a"
        kind = r.get("kind", "?")
        vp = r.get("vault_path") or r.get("source_url") or "—"
        content = (r.get("content") or "").strip().replace("\n", " ")[:200]
        print(f"\n#{i} [{kind}] sim={sim_s} src={vp}")
        print(f"   {content}")
    return 0


def _test_write() -> int:
    from memory.vault_writer import write_run_note

    written = write_run_note(
        run_id="test-write-" + datetime.now(timezone.utc).strftime("%H%M%S"),
        user_prompt=(
            "memory.__main__ test-write — verifies vault is reachable and "
            "the Workflows/scraping-runs/ write boundary is in force."
        ),
        plan_steps=[
            {
                "id": "s1",
                "query": "synthetic step (no execution)",
                "source": "memory.test_write",
                "tier": 0,
                "expected_yield": "a single .md file on disk",
                "rationale": "smoke test for write_run_note()",
            }
        ],
        results={"verdict": "ok", "rows_written": 1},
        lessons=[
            "vault_writer is functional",
            "if you can read this in Obsidian, the boundary check passed",
        ],
    )
    print(str(written))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return _usage()
    cmd = args[0]

    if cmd == "seed":
        return asyncio.run(_seed())
    if cmd == "search":
        if len(args) < 2:
            print("error: 'search' requires a query argument", file=sys.stderr)
            return _usage()
        return asyncio.run(_search(" ".join(args[1:])))
    if cmd == "test-write":
        return _test_write()

    print(f"error: unknown subcommand {cmd!r}", file=sys.stderr)
    return _usage()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
