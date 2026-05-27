"""
Tier 4 — Playwright stealth worker with human-like behaviour.

When the decision tree (`docs/ARCHITECTURE.md` §8) lands here:

- T2 (httpx + trafilatura) failed: probably anti-bot / WAF.
- T3 (crawl4ai) failed or the site needs a *persistent* logged-in/consented
  session that crawl4ai's stateless runs throw away.
- The site requires fine-grained human interaction (forms, mouse moves)
  that crawl4ai's high-level wrapper can't express cleanly.

Anti-detection
--------------
We deliberately stay on vanilla ``playwright`` (already a transitive dep via
crawl4ai) rather than pulling in ``playwright-stealth``. The bot fingerprint
giveaways that matter for most Swedish targets are:

- ``navigator.webdriver === true`` → override to ``undefined`` via init script.
- Empty ``navigator.plugins`` → spoof a tiny non-empty array.
- English-locale ``navigator.languages`` → set ``["sv-SE", "sv"]``.
- Headless-shaped viewport (e.g. 800x600) → pin a realistic 1366x768.
- Linear, instantaneous mouse paths → use ``bezier_path`` from
  ``_human_behavior`` to weave a curved trajectory.
- Robotic typing cadence → ``typing_cadence`` for per-character delays.

Persistent sessions
-------------------
Cookie consent dialogs (allabolag "Godkänn", many Swedish sites) survive
across runs because we persist the browser context's ``storage_state`` to
``backend/data/storage/<key>.json``. Subsequent runs with the same key reload
that state and skip the consent dance.

Setup
-----
Playwright the Python package is already installed (transitively via
crawl4ai). The headless Chromium binary is a separate ~150 MB download:

    .venv/Scripts/python.exe -m playwright install chromium

Without the browser binary ``stealth_fetch()`` returns a ``ScrapeResult``
with ``error`` populated rather than crashing — keeps the rest of the
``scrapers`` package importable on a fresh checkout.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import trafilatura

from ._human_behavior import (
    bezier_path,
    human_delay,
    random_user_agent,
    realistic_accept_language,
    realistic_scroll_pattern,
    realistic_viewport,
    typing_cadence,
)
from ._allabolag_strategy import AllabolagStrategy
from ._rate_limit import rate_limiter
from ._robots import robots_policy
from .base import ScrapeResult

import logging as _logging
_logger = _logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover — only for type checkers
    from playwright.async_api import Page

# ─────────────────────────────────────────────────────────────────────────────
# Soft import of Playwright — same pattern as crawl4ai_worker
# ─────────────────────────────────────────────────────────────────────────────

_PLAYWRIGHT_AVAILABLE: bool = False
_PLAYWRIGHT_IMPORT_ERROR: str | None = None

try:
    from playwright.async_api import (  # type: ignore[import-untyped]
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )

    _PLAYWRIGHT_AVAILABLE = True
except ImportError as exc:  # pragma: no cover — playwright should be present
    _PLAYWRIGHT_IMPORT_ERROR = (
        f"playwright not installed ({exc!s}). "
        "Run: pip install playwright && playwright install chromium"
    )

    class PlaywrightError(Exception):  # type: ignore[no-redef]
        """Fallback so the module still imports without playwright."""

    class PlaywrightTimeoutError(PlaywrightError):  # type: ignore[no-redef]
        """Fallback so the module still imports without playwright."""


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem layout for persistent storage + screenshots
# ─────────────────────────────────────────────────────────────────────────────
#
# These live under ``backend/data/`` (which the project .gitignore already
# excludes wholesale) so nothing user-specific ever lands in git.

_BACKEND_ROOT: Path = Path(__file__).resolve().parent.parent
_STORAGE_DIR: Path = _BACKEND_ROOT / "data" / "storage"
_SCREENSHOT_DIR: Path = _BACKEND_ROOT / "data" / "screenshots"


def _storage_state_path(key: str) -> Path:
    """Resolve the on-disk JSON path for a given storage_state key.

    Sanitises ``key`` to a safe filename — the orchestrator may pass a host
    or a free-form label and we don't want path traversal.
    """
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in key)
    safe = safe.strip("._") or "default"
    return _STORAGE_DIR / f"{safe}.json"


# ─────────────────────────────────────────────────────────────────────────────
# Anti-detection init script
# ─────────────────────────────────────────────────────────────────────────────
#
# Injected into every page (and every frame) before any author script runs.
# Keep it small — bigger payloads are themselves a fingerprint.

_STEALTH_INIT_SCRIPT: str = """
(() => {
    // Hide the Playwright/Selenium giveaway.
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    } catch (_) {}

    // Pretend to speak Swedish first.
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['sv-SE', 'sv'],
        });
    } catch (_) {}

    // Most headless Chromiums report 0 plugins. Spoof a small non-empty list.
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    { name: 'PDF Viewer' },
                    { name: 'Chrome PDF Viewer' },
                    { name: 'Chromium PDF Viewer' },
                ];
                Object.defineProperty(arr, 'length', { value: arr.length });
                return arr;
            },
        });
    } catch (_) {}

    // Chrome runtime hint — present on real Chrome, missing on headless.
    try {
        if (!window.chrome) {
            window.chrome = { runtime: {} };
        }
    } catch (_) {}
})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# Human-like helpers (Playwright-bound)
# ─────────────────────────────────────────────────────────────────────────────


async def _human_mouse_move(page: "Page", x: float, y: float) -> None:
    """Move the mouse along a quadratic Bezier with per-step delays.

    Picks a random start near the current viewport center (Playwright does
    not expose the live cursor position) and weaves to ``(x, y)`` along
    30-60 sampled points, sleeping a few milliseconds between each so the
    motion is observable to anti-bot heuristics that monitor pointer rate.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return

    # Use a viewport-anchored synthetic origin. We don't know where the
    # cursor "is" — Playwright resets to 0,0 on context start — but a
    # randomised origin near the center gives a more believable arc than
    # always starting at the origin.
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    sx = random.uniform(viewport["width"] * 0.3, viewport["width"] * 0.7)
    sy = random.uniform(viewport["height"] * 0.3, viewport["height"] * 0.7)

    steps = random.randint(30, 60)
    path = bezier_path((sx, sy), (float(x), float(y)), steps=steps)

    for px, py in path:
        try:
            await page.mouse.move(px, py)
        except PlaywrightError:
            # Move can fail mid-path if the page navigates; abort gracefully.
            return
        # ~5-15ms between samples → ~150-900ms for a 30-60 step move.
        await asyncio.sleep(random.uniform(0.005, 0.015))


async def _human_type(page: "Page", selector: str, text: str) -> bool:
    """Type ``text`` into ``selector`` with human cadence (80-180ms/char).

    Returns True on success, False if the element wasn't found in time.
    Punctuation triggers an extra 200-400ms pause via ``typing_cadence``.
    """
    if not _PLAYWRIGHT_AVAILABLE or not text:
        return False
    try:
        locator = page.locator(selector).first
        await locator.click(timeout=5000)
    except (PlaywrightTimeoutError, PlaywrightError):
        return False

    delays = typing_cadence(text)
    for ch, d in zip(text, delays, strict=True):
        try:
            await page.keyboard.type(ch)
        except PlaywrightError:
            return False
        await asyncio.sleep(d)
    return True


async def _human_scroll(page: "Page") -> None:
    """Scroll along a realistic reader pattern: chunks, pauses, occasional backtrack.

    Uses ``realistic_scroll_pattern`` (varied step sizes, pauses 0.4-1.8s,
    ~15% backtrack probability, final scroll to bottom) instead of the
    earlier fixed 2-4 scrolls. The pattern is computed once from the
    measured page height, then driven via ``window.scrollTo`` so we can
    backtrack precisely.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return

    # Measure document height — cheap; fall back to viewport height * 3 if
    # the evaluate fails (page navigated, CSP weirdness, etc.).
    try:
        page_height_raw = await page.evaluate(
            "Math.max(document.body.scrollHeight,"
            " document.documentElement.scrollHeight)"
        )
        page_height = int(page_height_raw) if page_height_raw else 0
    except PlaywrightError:
        viewport = page.viewport_size or {"width": 1366, "height": 768}
        page_height = viewport["height"] * 3

    if page_height <= 0:
        return

    pattern = realistic_scroll_pattern(page_height)
    for y, delay in pattern:
        try:
            await page.evaluate(f"window.scrollTo(0, {y});")
        except PlaywrightError:
            return
        await asyncio.sleep(delay)


async def _dismiss_cookie_consent(page: "Page", url: str) -> str | None:
    """Try a small battery of Swedish cookie-accept selectors.

    Returns the matched selector on success, ``None`` if nothing matched.
    Per-host hints (allabolag) come from ``AllabolagStrategy``; we then
    fall back to a generic Swedish-language list.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return None

    selectors: list[str] = []
    if AllabolagStrategy._is_allabolag(url):
        selectors.extend(AllabolagStrategy.cookie_button_selectors())

    # Generic fallbacks for any Swedish site.
    selectors.extend(
        [
            "button:has-text('Godkänn alla')",
            "button:has-text('Godkänn')",
            "button:has-text('Acceptera alla')",
            "button:has-text('Acceptera')",
            "button:has-text('Jag förstår')",
            "button:has-text('OK')",
            "[aria-label*='Godkänn']",
            "[aria-label*='Acceptera']",
        ]
    )

    # Dedupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for sel in selectors:
        if sel not in seen:
            seen.add(sel)
            ordered.append(sel)

    for sel in ordered:
        try:
            locator = page.locator(sel).first
            if await locator.count() == 0:
                continue
            # Short timeout — if the button is there, it's there now;
            # don't wait long for it to appear.
            await locator.click(timeout=2000)
            # Small human pause after click before continuing.
            await asyncio.sleep(0.3)
            return sel
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


async def stealth_fetch(
    url: str,
    *,
    storage_state_key: str = "default",
    wait_for_selector: str | None = None,
    timeout: float = 60,
    screenshot: bool = False,
    headless: bool = True,
    ignore_robots: bool = False,
    enforce_rate_limit: bool = True,
) -> ScrapeResult:
    """Fetch ``url`` with a Playwright Chromium context that mimics a human.

    Parameters
    ----------
    url:
        Absolute http(s) URL.
    storage_state_key:
        Filename stem under ``backend/data/storage/``. The Playwright
        ``storage_state`` (cookies + localStorage) is loaded from
        ``<key>.json`` if it exists and saved back after the page load —
        consent banners and login cookies persist across runs.
    wait_for_selector:
        If set, wait for this CSS selector to be visible before considering
        the page "loaded" (useful for SPAs where ``networkidle`` is unreliable).
    timeout:
        Page-load + selector-wait timeout in seconds. Default 60s.
    screenshot:
        If True, save a PNG snapshot under ``backend/data/screenshots/`` and
        record its path in ``metadata["screenshot_path"]``.
    headless:
        Default True. Set False for interactive debugging — useful with the
        CLI's ``--no-headless`` flag.

    Returns
    -------
    ``ScrapeResult`` with ``tier=4``. On any failure (no Chromium binary,
    navigation timeout, anti-bot block) the result has ``error`` set and
    ``ok == False``; this function never raises.
    """
    if not url:
        return ScrapeResult(tier=4, url=url, error="Empty URL")

    if not _PLAYWRIGHT_AVAILABLE:
        return ScrapeResult(
            tier=4,
            url=url,
            error=_PLAYWRIGHT_IMPORT_ERROR
            or "playwright not installed — pip install playwright",
        )

    # robots.txt enforcement (fail-open on fetch errors).
    if not ignore_robots:
        if not await robots_policy.is_allowed(url, "*"):
            return ScrapeResult(
                tier=4,
                url=url,
                error=f"robots.txt disallow for {url}",
                metadata={"policy_block": "robots"},
            )
    else:
        _logger.warning("stealth_fetch: ignoring robots.txt for %s", url)

    # Ensure data dirs exist (cheap, idempotent).
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if screenshot:
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    state_path = _storage_state_path(storage_state_key)
    metadata: dict[str, Any] = {
        "storage_state_key": storage_state_key,
        "storage_state_loaded": state_path.exists(),
        "headless": headless,
    }

    # Per-domain rate limit (before launching a whole browser).
    if enforce_rate_limit:
        metadata["rate_limit_waited_seconds"] = await rate_limiter.acquire(url)

    # Human delay before we even launch — staggers fleets of parallel workers.
    await asyncio.sleep(human_delay(mean_seconds=2.0))

    timeout_ms = int(timeout * 1000)

    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=headless)
            except PlaywrightError as exc:
                msg = str(exc)
                hint = ""
                if "Executable doesn't exist" in msg or "browserType.launch" in msg:
                    hint = (
                        " — run: .venv/Scripts/python.exe -m playwright "
                        "install chromium"
                    )
                return ScrapeResult(
                    tier=4,
                    url=url,
                    metadata=metadata,
                    error=f"Playwright launch failed: {msg}{hint}",
                )

            vp_w, vp_h = realistic_viewport()
            context_kwargs: dict[str, Any] = {
                "user_agent": random_user_agent(),
                "viewport": {"width": vp_w, "height": vp_h},
                "locale": "sv-SE",
                "timezone_id": "Europe/Stockholm",
                "extra_http_headers": {
                    "Accept-Language": realistic_accept_language("SE"),
                },
            }
            metadata["viewport"] = {"width": vp_w, "height": vp_h}
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)

            context = await browser.new_context(**context_kwargs)
            # Inject anti-detection BEFORE any page script runs.
            await context.add_init_script(_STEALTH_INIT_SCRIPT)

            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                metadata["status_code"] = (
                    response.status if response is not None else None
                )
                metadata["final_url"] = page.url

                # Try to dismiss the consent banner; failure is non-fatal.
                dismissed = await _dismiss_cookie_consent(page, url)
                metadata["cookie_consent_dismissed"] = dismissed

                # Optional explicit selector wait (SPAs).
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector,
                            timeout=timeout_ms,
                            state="visible",
                        )
                        metadata["selector_matched"] = wait_for_selector
                    except PlaywrightTimeoutError:
                        metadata["selector_matched"] = False

                # Human scroll to trigger lazy-loaded content + look natural.
                await _human_scroll(page)

                # A small mouse weave so heuristics that watch pointer
                # activity see a non-zero rate.
                viewport = page.viewport_size or {"width": 1366, "height": 768}
                await _human_mouse_move(
                    page,
                    x=viewport["width"] * 0.5,
                    y=viewport["height"] * 0.5,
                )

                # Grab content.
                html = await page.content()
                try:
                    content_text = await page.inner_text("body")
                except PlaywrightError:
                    content_text = None
                title = await page.title()

                # Optional screenshot.
                if screenshot:
                    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                    shot_path = _SCREENSHOT_DIR / f"{ts}.png"
                    try:
                        await page.screenshot(
                            path=str(shot_path),
                            full_page=True,
                        )
                        metadata["screenshot_path"] = str(shot_path)
                    except PlaywrightError as exc:
                        metadata["screenshot_error"] = str(exc)

                # Persist storage_state so the next call reuses consent /
                # login cookies.
                try:
                    await context.storage_state(path=str(state_path))
                    metadata["storage_state_saved"] = str(state_path)
                except PlaywrightError as exc:
                    metadata["storage_state_save_error"] = str(exc)

            except PlaywrightTimeoutError as exc:
                await context.close()
                await browser.close()
                return ScrapeResult(
                    tier=4,
                    url=url,
                    metadata=metadata,
                    error=f"Playwright timeout for {url}: {exc!s}",
                )
            except PlaywrightError as exc:
                await context.close()
                await browser.close()
                return ScrapeResult(
                    tier=4,
                    url=url,
                    metadata=metadata,
                    error=f"Playwright error for {url}: {exc!s}",
                )

            await context.close()
            await browser.close()
    except Exception as exc:  # noqa: BLE001 — surface anything unexpected
        return ScrapeResult(
            tier=4,
            url=url,
            metadata=metadata,
            error=f"Playwright session failed for {url}: {exc!s}",
        )

    # Best-effort markdown via trafilatura on the rendered HTML — gives the
    # LLM the same clean shape T2/T3 produce.
    content_markdown: str | None = None
    try:
        content_markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001
        metadata["trafilatura_md_error"] = str(exc)

    return ScrapeResult(
        tier=4,
        url=url,
        title=title or None,
        content_text=content_text,
        content_markdown=content_markdown,
        raw_html_excerpt=html[:500] if html else None,
        metadata=metadata,
    )


__all__ = ["stealth_fetch"]
