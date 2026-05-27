"""
Shared LLM provider factory for scraper tiers that need an LLM.

Both T3 (`crawl4ai_worker`) and T5 (`browseruse_t5`) optionally need a
langchain-compatible chat model. Right now T3 has its own crawl4ai-native
`LLMConfig` path (LiteLLM under the hood); T5 talks to ``browser-use`` which
expects a langchain ``BaseChatModel`` instance. This helper provides the
latter in one place so a future T3 refactor can share it.

Cost model
----------
- ``ollama`` (default) — local LLM via `langchain-ollama`. Cost-zero,
  slower, runs against ``OLLAMA_BASE_URL`` (default
  ``http://localhost:11434``) using ``OLLAMA_MODEL_REASONING``
  (default ``llama3.1:8b``).
- ``groq`` — cloud LLM via `langchain-groq`. Free tier today, may move to
  pay-per-token later. Requires ``GROQ_API_KEY`` in the environment. Model
  pinned to ``llama-3.3-70b-versatile`` to match the rest of the project.

Soft imports
------------
``langchain-ollama`` and ``langchain-groq`` are heavy optional deps — they
ship with their own transitive trees (httpx, pydantic-settings, etc.) and
add ~50 MB to the venv. We keep them out of the core install and import
them lazily so the rest of the scrapers module stays usable on a fresh
checkout. When a caller asks for a provider whose backing library is not
installed, we raise a ``RuntimeError`` with a clear ``pip install ...``
hint — never silently degrade.
"""

from __future__ import annotations

import os
from typing import Any

_DEFAULT_OLLAMA_BASE = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _try_import_ollama() -> tuple[Any | None, str | None]:
    """Return ``(ChatOllama class, None)`` on success, ``(None, error)`` else."""
    try:
        from langchain_ollama import ChatOllama  # type: ignore[import-untyped]
    except ImportError as exc:
        return None, (
            f"langchain_ollama not available ({exc!s}). "
            "Install: pip install langchain-ollama"
        )
    return ChatOllama, None


def _try_import_groq() -> tuple[Any | None, str | None]:
    """Return ``(ChatGroq class, None)`` on success, ``(None, error)`` else."""
    try:
        from langchain_groq import ChatGroq  # type: ignore[import-untyped]
    except ImportError as exc:
        return None, (
            f"langchain_groq not available ({exc!s}). "
            "Install: pip install langchain-groq"
        )
    return ChatGroq, None


def get_llm(provider: str = "ollama") -> Any:
    """Build a langchain-compatible chat model for ``provider``.

    Parameters
    ----------
    provider:
        ``"ollama"`` (default, cost-zero, local) or ``"groq"`` (cloud, free
        tier today). Case-insensitive.

    Returns
    -------
    A langchain ``BaseChatModel`` instance ready to pass to
    ``browser_use.Agent(..., llm=...)`` or any other langchain consumer.

    Raises
    ------
    RuntimeError
        - The provider name is unknown.
        - The backing langchain integration package is not installed.
        - The provider's required env var (``GROQ_API_KEY``) is missing.

    Callers that need a non-raising version should wrap this in a
    ``try/except RuntimeError`` and return their own ``ScrapeResult`` with
    an ``error`` field — the scraper tiers do exactly that.
    """
    name = (provider or "").strip().lower()

    if name == "ollama":
        ChatOllama, err = _try_import_ollama()
        if ChatOllama is None:
            raise RuntimeError(
                err or "provider ollama not available: install langchain_ollama"
            )
        base_url = os.environ.get("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE)
        model = os.environ.get("OLLAMA_MODEL_REASONING", _DEFAULT_OLLAMA_MODEL)
        return ChatOllama(model=model, base_url=base_url)

    if name == "groq":
        ChatGroq, err = _try_import_groq()
        if ChatGroq is None:
            raise RuntimeError(
                err or "provider groq not available: install langchain_groq"
            )
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY not set — cannot use groq provider. "
                "Put it in backend/.env."
            )
        return ChatGroq(model=_DEFAULT_GROQ_MODEL, api_key=api_key)

    raise RuntimeError(
        f"Unknown llm provider {provider!r} (expected 'ollama' or 'groq')"
    )


__all__ = ["get_llm"]
