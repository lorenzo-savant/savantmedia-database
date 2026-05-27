"""
Ollama embeddings client — Fase 11 (`docs/ARCHITECTURE.md` §7).

Thin async wrapper around the Ollama `/api/embeddings` endpoint, used by
the memory layer to embed knowledge chunks (playbooks, lessons, query
logs) with `nomic-embed-text` (768-dim).

Design constraints
------------------
- **Graceful degradation**: if Ollama is unreachable or returns a malformed
  payload, `embed()` returns `None` and logs a warning. Callers must handle
  the None case (the memory writer inserts the row with `embedding=NULL`
  so the data is still visible, just not in vector search).
- **Async-first**: both functions are `async` to integrate with the
  LangGraph async nodes.
- **Bounded concurrency** in `embed_batch`: max 4 simultaneous Ollama
  requests via a semaphore — the local Ollama is single-GPU and saturates
  fast otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

log = logging.getLogger("savantsdatabas.memory.embeddings")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_TIMEOUT_S = 30.0
_MAX_CONCURRENT = 4


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


async def embed(text: str, model: str = _DEFAULT_MODEL) -> list[float] | None:
    """Embed a single string via Ollama.

    Returns a 768-dim float list on success, or None on any failure
    (network error, non-200, malformed payload, empty text).
    """
    if not text or not text.strip():
        return None

    url = f"{_ollama_base_url()}/api/embeddings"
    payload = {"model": model, "prompt": text}

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("embed: Ollama call failed (%s): %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never break the graph
        log.warning("embed: unexpected error (%s): %s", url, exc)
        return None

    vec = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(vec, list) or not vec:
        log.warning("embed: malformed Ollama response: keys=%s", list(data.keys()) if isinstance(data, dict) else type(data))
        return None

    # Coerce to plain floats; pgvector wants numeric vectors.
    try:
        return [float(x) for x in vec]
    except (TypeError, ValueError) as exc:
        log.warning("embed: non-numeric values in embedding: %s", exc)
        return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed a batch concurrently (max 4 in-flight).

    Returns a list of same length as `texts`, with `None` for any
    individual failure. Order is preserved.
    """
    if not texts:
        return []

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _bounded(t: str) -> list[float] | None:
        async with sem:
            return await embed(t)

    return await asyncio.gather(*(_bounded(t) for t in texts))
