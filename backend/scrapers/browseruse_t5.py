"""
Tier 5 — ``browser-use`` autonomous agent worker.

🔴 vault priority — autonomous browser agent. Used for decision-driven
flows (forms, logins). Zero cost via Ollama.

When the decision tree (`docs/ARCHITECTURE.md` §8) lands here:

- T4 (Playwright stealth) ran, the page loaded, but the *flow* requires
  intelligent navigation — e.g. "click the right button out of five",
  "fill a multi-step form where the next page depends on what you typed",
  "log in through a two-step OAuth that branches on captcha".
- The task can be expressed as a natural-language instruction the agent
  decides how to satisfy. If you already know the exact selector path,
  stay on T4 — T5 is an order of magnitude more expensive in tokens and
  wall-clock.

About browser-use
-----------------
`browser-use <https://github.com/browser-use/browser-use>`_ is a Python
framework for AI-driven browser automation. It sits between an LLM and
Playwright: the LLM decides "click this button / type that text /
extract this field", and browser-use executes those actions against a
real Chromium. It is LLM-agnostic — OpenAI, Anthropic, Groq, and Ollama
all work through a langchain-compatible chat model.

For Savantsdatabas we default to **Ollama** (cost-zero, local,
``OLLAMA_MODEL_REASONING``) and offer **Groq** for the free tier when
speed matters more than locality.

Soft import
-----------
``browser-use`` is heavy (Playwright + the agent framework + langchain
deps) and entirely optional. The rest of the scrapers module must keep
importing fine without it; ``autonomous_navigate()`` returns a
``ScrapeResult`` with ``error`` populated rather than crashing.

Setup (only when you actually need T5):

    .venv/Scripts/python.exe -m pip install "browser-use[ollama]"
    .venv/Scripts/python.exe -m playwright install chromium

Anti-bot policy
---------------
Same as T4: respect ``robots.txt``, respect host rate limits, no
aggressive scraping of authenticated content. The agent runs headless by
default and waits a Poisson-distributed delay before the first action so
parallel workers don't stampede.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ._human_behavior import human_delay
from ._llm_provider import get_llm
from .base import ScrapeResult

# ─────────────────────────────────────────────────────────────────────────────
# Soft import of browser-use — same pattern as T3/T4 workers.
# ─────────────────────────────────────────────────────────────────────────────

_BROWSER_USE_AVAILABLE: bool = False
_BROWSER_USE_IMPORT_ERROR: str | None = None

try:
    from browser_use import Agent  # type: ignore[import-untyped]

    # ``BrowserConfig`` / ``Browser`` moved between browser-use releases; try
    # both well-known import paths and tolerate either being absent. We only
    # need them to thread ``headless`` through to the underlying Playwright,
    # so missing them is non-fatal — we just lose the headless toggle.
    try:
        from browser_use import Browser, BrowserConfig  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — older / newer browser-use
        try:
            from browser_use.browser.browser import (  # type: ignore[import-untyped,no-redef]
                Browser,
                BrowserConfig,
            )
        except ImportError:
            Browser = None  # type: ignore[assignment,misc]
            BrowserConfig = None  # type: ignore[assignment,misc]

    _BROWSER_USE_AVAILABLE = True
except ImportError as exc:  # pragma: no cover — only on machines without browser-use
    _BROWSER_USE_IMPORT_ERROR = (
        f"browser-use not installed ({exc!s}). "
        "Run: pip install browser-use && playwright install chromium"
    )
    Agent = None  # type: ignore[assignment,misc]
    Browser = None  # type: ignore[assignment,misc]
    BrowserConfig = None  # type: ignore[assignment,misc]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _compose_task(task: str, start_url: str | None) -> str:
    """Prepend a ``start_url`` hint to the natural-language task.

    browser-use's ``Agent(task=...)`` is just a prompt; the most reliable
    way to seed the agent on a specific URL is to mention it in the first
    sentence. We do it here so the caller stays declarative.
    """
    task = (task or "").strip()
    if not start_url:
        return task
    return f"First, navigate to {start_url}. Then: {task}"


def _extract_action_trail(history: Any) -> list[dict[str, Any]]:
    """Best-effort projection of browser-use's ``AgentHistory`` into a list.

    The internal representation has churned across browser-use releases
    (``model_outputs``/``model_actions``/``history``). We try the
    high-level public methods first, fall back to introspecting the
    underlying steps, and as a last resort return an empty list — the
    caller's ``metadata["actions"]`` is debug info, not load-bearing.
    """
    if history is None:
        return []

    # browser-use 0.1.x exposes ``model_actions()`` and ``model_outputs()``
    actions: list[Any] | None = None
    for method_name in ("model_actions", "extracted_content"):
        method = getattr(history, method_name, None)
        if callable(method):
            try:
                candidate = method()
            except Exception:  # noqa: BLE001 — defensive, this is metadata
                continue
            if isinstance(candidate, list) and candidate:
                actions = candidate
                break

    if actions is None:
        # Last resort: iterate ``.history`` if it looks like a list.
        steps = getattr(history, "history", None)
        if isinstance(steps, list):
            actions = steps
        else:
            return []

    trail: list[dict[str, Any]] = []
    for i, item in enumerate(actions, start=1):
        # ``item`` is often a pydantic model — model_dump if available.
        if hasattr(item, "model_dump"):
            try:
                payload = item.model_dump()
            except Exception:  # noqa: BLE001
                payload = {"repr": repr(item)}
        elif isinstance(item, dict):
            payload = item
        else:
            payload = {"repr": repr(item)}
        trail.append({"step": i, "action": payload})
    return trail


def _extract_final_result(history: Any) -> tuple[str | None, str | None]:
    """Return ``(content_text, final_url)`` from an ``AgentHistory``.

    Tries the documented public methods (``final_result``, ``urls``); each
    one is wrapped because the API surface has not stabilised across
    browser-use versions.
    """
    if history is None:
        return None, None

    content_text: str | None = None
    final_url: str | None = None

    final = getattr(history, "final_result", None)
    if callable(final):
        try:
            value = final()
            if isinstance(value, str):
                content_text = value
            elif value is not None:
                content_text = str(value)
        except Exception:  # noqa: BLE001
            content_text = None

    urls = getattr(history, "urls", None)
    if callable(urls):
        try:
            url_list = urls()
            if isinstance(url_list, list) and url_list:
                # Last URL the agent visited — most relevant for audit.
                final_url = str(url_list[-1])
        except Exception:  # noqa: BLE001
            final_url = None

    return content_text, final_url


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


async def autonomous_navigate(
    *,
    task: str,
    start_url: str | None = None,
    llm_provider: str = "ollama",
    max_steps: int = 20,
    timeout: float = 180,
    screenshot: bool = False,
    headless: bool = True,
) -> ScrapeResult:
    """Run a ``browser-use`` agent against ``task``.

    Parameters
    ----------
    task:
        Natural-language instruction the agent will try to satisfy. E.g.
        ``"Open foretag.se/kontakt and find the CFO's email"`` or
        ``"Navigera till linkedin.com/in/anna-lindberg och hitta hennes
        nuvarande arbetsgivare"``. Required.
    start_url:
        Optional URL to seed the agent on. If provided, it is prepended
        to the task as a "First, navigate to ..." sentence.
    llm_provider:
        ``"ollama"`` (default, cost-zero, local) or ``"groq"`` (cloud,
        free tier today). See ``_llm_provider.get_llm``.
    max_steps:
        Cap on agent reasoning + action steps. Default 20 — keeps token
        cost bounded if the agent gets stuck in a loop.
    timeout:
        Wall-clock timeout (seconds) for the full agent run. Default
        180s. Exceeding it returns a ``ScrapeResult`` with ``error``.
    screenshot:
        Reserved for future use — browser-use's per-step screenshot
        recording is on by default and lives inside its own
        ``AgentHistory``. We record only whether the caller asked for it
        in ``metadata["screenshot_requested"]``.
    headless:
        Default ``True``. Pass ``False`` for interactive debugging
        (CLI's ``--no-headless`` flag).

    Returns
    -------
    ``ScrapeResult`` with ``tier=5``. On any failure (browser-use missing,
    LLM unreachable, agent timeout, Playwright crash) the result has
    ``error`` set and ``ok == False``; this function never raises.
    """
    task = (task or "").strip()
    if not task:
        return ScrapeResult(tier=5, error="Empty task — browser-use needs a goal")

    if not _BROWSER_USE_AVAILABLE or Agent is None:
        return ScrapeResult(
            tier=5,
            url=start_url,
            error=_BROWSER_USE_IMPORT_ERROR
            or (
                "browser-use not installed. "
                "Run: pip install browser-use && playwright install chromium"
            ),
        )

    metadata: dict[str, Any] = {
        "task": task,
        "start_url": start_url,
        "llm_provider": llm_provider,
        "max_steps": max_steps,
        "headless": headless,
        "screenshot_requested": screenshot,
    }

    # ── Resolve LLM (soft-import via the shared factory) ─────────────────
    try:
        llm = get_llm(llm_provider)
    except RuntimeError as exc:
        return ScrapeResult(
            tier=5,
            url=start_url,
            metadata=metadata,
            error=f"LLM unavailable: {exc!s}",
        )

    # ── Polite delay before launching anything ───────────────────────────
    await asyncio.sleep(human_delay(mean_seconds=2.0))

    # ── Build the Agent. The constructor signature has churned across
    #    browser-use releases — pass kwargs and fall back if rejected.
    composed_task = _compose_task(task, start_url)

    agent_kwargs: dict[str, Any] = {"task": composed_task, "llm": llm}

    # Thread headless through to the underlying Playwright when possible.
    # browser-use 0.1.x: pass a ``Browser(config=BrowserConfig(headless=...))``.
    if not headless and Browser is not None and BrowserConfig is not None:
        try:
            agent_kwargs["browser"] = Browser(config=BrowserConfig(headless=False))
        except Exception as exc:  # noqa: BLE001
            metadata["browser_config_error"] = str(exc)

    try:
        agent = Agent(**agent_kwargs)
    except TypeError:
        # Older browser-use that doesn't accept ``browser=`` — retry minimal.
        try:
            agent = Agent(task=composed_task, llm=llm)
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                tier=5,
                url=start_url,
                metadata=metadata,
                error=f"browser-use Agent init failed: {exc!s}",
            )
    except Exception as exc:  # noqa: BLE001
        return ScrapeResult(
            tier=5,
            url=start_url,
            metadata=metadata,
            error=f"browser-use Agent init failed: {exc!s}",
        )

    # ── Run the agent with an overall wall-clock timeout ─────────────────
    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=max_steps),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return ScrapeResult(
            tier=5,
            url=start_url,
            metadata=metadata,
            error=f"browser-use agent timed out after {timeout}s",
        )
    except Exception as exc:  # noqa: BLE001 — agent surfaces many error types
        return ScrapeResult(
            tier=5,
            url=start_url,
            metadata=metadata,
            error=f"browser-use agent failed: {exc!s}",
        )

    # ── Project history into ScrapeResult shape ──────────────────────────
    content_text, final_url = _extract_final_result(history)
    metadata["actions"] = _extract_action_trail(history)
    metadata["steps_taken"] = len(metadata["actions"])

    return ScrapeResult(
        tier=5,
        url=final_url or start_url,
        content_text=content_text,
        metadata=metadata,
    )


__all__ = ["autonomous_navigate"]
