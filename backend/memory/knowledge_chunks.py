"""
pgvector ingest + semantic search — Fase 11 (`docs/ARCHITECTURE.md` §7).

Bridges:
- the vault (Obsidian MD playbooks/lessons → `public.knowledge_chunks` rows)
- the agent (lessons learned post-run → upserted as `kind='lesson'`)
- the recall node (semantic search over chunks)

Graceful degradation
--------------------
- If Ollama is unreachable, `upsert_chunk` still inserts the row with
  `embedding=NULL` — the data stays visible to the operator via the
  cockpit UI; only vector search misses it.
- If Supabase is unreachable, every function returns `None` / `[]` and
  logs a warning — the LangGraph node above keeps running.
- `ingest_vault_seed()` is idempotent: it queries first to skip files
  whose `vault_path` is already present in `knowledge_chunks`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from .embeddings import embed
from .vault_writer import vault_path

log = logging.getLogger("savantsdatabas.memory.knowledge_chunks")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_CHUNK_TARGET = 1500
_CHUNK_TRIGGER = 2000

# Vault seed files (must exist; missing ones are skipped with a warning).
_SEED_FILES = [
    Path("Projects") / "🕷️ Web Scraping & SERP.md",
    Path("Projects") / "🧠 AI Agents & Infrastructure.md",
    Path("Projects") / "🔍 OSINT.md",
    Path("Environments") / "🕷️ Scraping Lab.md",
]


# ─────────────────────────────────────────────────────────────────────────────
# Supabase access (lazy, never raises at import time)
# ─────────────────────────────────────────────────────────────────────────────


def _get_sb() -> Any | None:
    """Return a Supabase client, or None if env is unconfigured / import fails."""
    try:
        from api.deps import get_supabase  # type: ignore
        return get_supabase()
    except Exception as exc:  # noqa: BLE001
        log.warning("knowledge_chunks: Supabase unavailable (%s)", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# upsert_chunk
# ─────────────────────────────────────────────────────────────────────────────


async def upsert_chunk(
    *,
    kind: str,
    content: str,
    metadata: dict,
    vault_path: str | None = None,
    source_url: str | None = None,
) -> str | None:
    """Embed `content` and INSERT a row in `public.knowledge_chunks`.

    Embedding failures are tolerated: the row is inserted with NULL
    embedding so the lesson/snippet is still browsable in the cockpit.

    Returns the inserted row's UUID (str), or None on Supabase failure.
    """
    sb = _get_sb()
    if sb is None:
        return None

    text = (content or "").strip()
    if not text:
        log.warning("upsert_chunk: skipping empty content (kind=%s)", kind)
        return None

    vec = await embed(text)
    row: dict[str, Any] = {
        "kind": kind,
        "content": text,
        "metadata": metadata or {},
    }
    if vec is not None:
        row["embedding"] = vec
    if vault_path is not None:
        row["vault_path"] = vault_path
    if source_url is not None:
        row["source_url"] = source_url

    try:
        # supabase-py is sync; offload to thread to keep the async caller happy.
        resp = await asyncio.to_thread(
            lambda: sb.table("knowledge_chunks").insert(row).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_chunk: insert failed (kind=%s): %s", kind, exc)
        return None

    data = getattr(resp, "data", None) or []
    if not data:
        log.warning("upsert_chunk: insert returned no rows (kind=%s)", kind)
        return None
    rid = data[0].get("id")
    log.info("upsert_chunk: inserted id=%s kind=%s embedded=%s", rid, kind, vec is not None)
    return str(rid) if rid else None


# ─────────────────────────────────────────────────────────────────────────────
# ingest_vault_note
# ─────────────────────────────────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def _chunk_text(text: str) -> list[str]:
    """Split long markdown into ≤_CHUNK_TARGET-char chunks on paragraph breaks."""
    if len(text) <= _CHUNK_TRIGGER:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        candidate = (cur + "\n\n" + p).strip() if cur else p
        if len(candidate) > _CHUNK_TARGET and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        chunks.append(cur)

    # Hard split anything that's still too big (rare: one paragraph > target).
    final: list[str] = []
    for c in chunks:
        if len(c) <= _CHUNK_TARGET:
            final.append(c)
        else:
            for i in range(0, len(c), _CHUNK_TARGET):
                final.append(c[i : i + _CHUNK_TARGET])
    return final


async def ingest_vault_note(path: Path, kind: str = "playbook") -> list[str]:
    """Read a vault MD, chunk it, upsert each chunk. Returns inserted ids.

    `path` may be absolute or relative to `vault_path()`. The stored
    `vault_path` field is always the path *relative to* the vault root,
    using forward slashes (Obsidian convention).
    """
    p = Path(path)
    if not p.is_absolute():
        p = vault_path() / p

    if not p.exists() or not p.is_file():
        log.warning("ingest_vault_note: file not found: %s", p)
        return []

    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest_vault_note: read failed (%s): %s", p, exc)
        return []

    body = _strip_frontmatter(raw)
    if not body:
        log.warning("ingest_vault_note: empty body after frontmatter strip: %s", p)
        return []

    # Path relative to vault root, forward slashes.
    try:
        rel = p.relative_to(vault_path()).as_posix()
    except (ValueError, FileNotFoundError):
        rel = p.as_posix()

    chunks = _chunk_text(body)
    log.info("ingest_vault_note: %s → %d chunk(s)", rel, len(chunks))

    ids: list[str] = []
    for i, ch in enumerate(chunks):
        cid = await upsert_chunk(
            kind=kind,
            content=ch,
            metadata={
                "source_file": rel,
                "chunk_index": i,
                "chunk_count": len(chunks),
            },
            vault_path=rel,
        )
        if cid:
            ids.append(cid)
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# ingest_vault_seed (one-shot, idempotent)
# ─────────────────────────────────────────────────────────────────────────────


async def ingest_vault_seed() -> dict[str, list[str]]:
    """Ingest the canonical playbook files into `knowledge_chunks`.

    Idempotent: skips files whose `vault_path` is already present.
    Returns `{relative_path: [chunk_ids]}` for files actually ingested
    (empty list for files that were skipped).
    """
    sb = _get_sb()
    if sb is None:
        log.warning("ingest_vault_seed: Supabase unavailable, aborting")
        return {}

    try:
        vroot = vault_path()
    except FileNotFoundError as exc:
        log.warning("ingest_vault_seed: %s", exc)
        return {}

    # Probe existing vault_paths so we don't re-ingest.
    try:
        existing_resp = await asyncio.to_thread(
            lambda: sb.table("knowledge_chunks")
            .select("vault_path")
            .not_.is_("vault_path", "null")
            .execute()
        )
        existing = {row.get("vault_path") for row in (existing_resp.data or [])}
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest_vault_seed: probe failed (%s) — proceeding without skip-list", exc)
        existing = set()

    out: dict[str, list[str]] = {}
    for rel in _SEED_FILES:
        rel_posix = rel.as_posix()
        full = vroot / rel
        if not full.exists():
            log.warning("ingest_vault_seed: missing %s", full)
            out[rel_posix] = []
            continue
        if rel_posix in existing:
            log.info("ingest_vault_seed: skip (already ingested): %s", rel_posix)
            out[rel_posix] = []
            continue
        ids = await ingest_vault_note(full, kind="playbook")
        out[rel_posix] = ids

    return out


# ─────────────────────────────────────────────────────────────────────────────
# semantic_search
# ─────────────────────────────────────────────────────────────────────────────


async def semantic_search(
    query: str,
    limit: int = 5,
    kind: str | None = None,
) -> list[dict]:
    """Vector-search `knowledge_chunks` for chunks closest to `query`.

    Strategy:
      1. Embed the query (Ollama).
      2. Try Supabase RPC `match_knowledge_chunks` (if deployed).
      3. Fallback: fetch up to 500 candidate rows (optionally filtered
         by kind) and compute cosine distance client-side.

    Each result dict includes `similarity` (1 - cosine_distance, range
    [-1, 1]; higher = closer).
    """
    if not query or not query.strip():
        return []

    sb = _get_sb()
    if sb is None:
        return []

    qvec = await embed(query)
    if qvec is None:
        log.warning("semantic_search: embedding failed, returning empty result")
        return []

    # Attempt 1: RPC (preferred — runs HNSW index server-side).
    try:
        rpc_payload: dict[str, Any] = {
            "query_embedding": qvec,
            "match_count": limit,
        }
        if kind:
            rpc_payload["filter_kind"] = kind
        rpc_resp = await asyncio.to_thread(
            lambda: sb.rpc("match_knowledge_chunks", rpc_payload).execute()
        )
        rows = rpc_resp.data or []
        if rows:
            return list(rows)[:limit]
    except Exception as exc:  # noqa: BLE001
        log.debug("semantic_search: RPC unavailable (%s) — falling back to client-side", exc)

    # Attempt 2: client-side cosine on a bounded sample.
    try:
        q = sb.table("knowledge_chunks").select(
            "id, kind, content, metadata, vault_path, source_url, embedding"
        )
        if kind:
            q = q.eq("kind", kind)
        resp = await asyncio.to_thread(lambda: q.limit(500).execute())
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_search: fallback fetch failed: %s", exc)
        return []

    rows = resp.data or []
    scored: list[tuple[float, dict]] = []
    qv = qvec
    qnorm = sum(x * x for x in qv) ** 0.5 or 1.0
    for row in rows:
        emb = row.get("embedding")
        if not emb:
            continue
        # Supabase may return embedding as str ("[0.1,0.2,...]") for pgvector.
        if isinstance(emb, str):
            try:
                emb = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
            except ValueError:
                continue
        if not isinstance(emb, list) or len(emb) != len(qv):
            continue
        dot = sum(a * b for a, b in zip(qv, emb))
        en = sum(x * x for x in emb) ** 0.5 or 1.0
        sim = dot / (qnorm * en)
        # drop embedding from the response to keep payload light
        row_out = {k: v for k, v in row.items() if k != "embedding"}
        row_out["similarity"] = sim
        scored.append((sim, row_out))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint (used by `python -m memory.knowledge_chunks`)
# ─────────────────────────────────────────────────────────────────────────────


def _cli_main() -> None:
    """Run ingest_vault_seed() from the CLI."""
    import json

    async def go() -> None:
        out = await ingest_vault_seed()
        print(json.dumps({k: len(v) for k, v in out.items()}, indent=2))

    asyncio.run(go())


if __name__ == "__main__":  # pragma: no cover
    _cli_main()
