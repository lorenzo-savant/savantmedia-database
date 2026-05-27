"""
Small utility module that codifies human-like behaviour for the scraper tiers
(see `docs/ARCHITECTURE.md` §8 — "Comportamento umano — implementazione").

Keep this module dependency-free (stdlib only): every scraper tier imports it,
so any heavy import here costs us on every fetch.
"""

from __future__ import annotations

import math
import random
from urllib.parse import urlparse


# ─────────────────────────────────────────────────────────────────────────────
# Curated User-Agent pool — realistic, recent browsers, mixed OS/family
# ─────────────────────────────────────────────────────────────────────────────
#
# Rule of thumb (vault `🕷️ Web Scraping & SERP`): never advertise httpx/python.
# Mix Chrome/Firefox/Edge over Windows/macOS so request fingerprints rotate.

_USER_AGENTS: tuple[str, ...] = (
    # Chrome 124 — Windows 10/11
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 — macOS 14 Sonoma
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox 125 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Firefox 125 — Ubuntu
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Edge 124 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Safari 17 — macOS Sonoma
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
)


def random_user_agent() -> str:
    """Return a realistic browser User-Agent string at random."""
    return random.choice(_USER_AGENTS)


# ─────────────────────────────────────────────────────────────────────────────
# Human-like delays — Poisson / exponential inter-arrival
# ─────────────────────────────────────────────────────────────────────────────


def human_delay(mean_seconds: float = 2.5) -> float:
    """Return a Poisson-distributed delay in seconds.

    Inter-arrival times of a Poisson process follow an exponential
    distribution with mean = `mean_seconds`. That produces bursty, human-
    looking pacing (vs the uniform metronome of `time.sleep(2)`).

    Clamped to ``[0.5, 10.0]`` seconds to avoid degenerate cases (instant
    or pathologically long waits).
    """
    if mean_seconds <= 0:
        return 0.5
    # Inverse-CDF sampling of Exp(1/mean)
    u = random.random()
    # random() can return 0 but not 1 — guard 0 to avoid log(1).
    u = max(u, 1e-9)
    d = -math.log(u) * mean_seconds
    return max(0.5, min(10.0, d))


# ─────────────────────────────────────────────────────────────────────────────
# Swedish business domain heuristic
# ─────────────────────────────────────────────────────────────────────────────
#
# Used by the orchestrator to know "this URL is likely a Swedish company
# resource → prefer T0 open data if possible, otherwise plan T2/T3".

_KNOWN_SWEDISH_BUSINESS_HOSTS: frozenset[str] = frozenset(
    {
        "allabolag.se",
        "bolagsfakta.se",
        "merinfo.se",
        "ratsit.se",
        "hitta.se",
        "eniro.se",
        "bolagsverket.se",
        "scb.se",
        "apiverket.se",
        "proff.se",
        "largestcompanies.se",
    }
)


def is_swedish_business_domain(url: str) -> bool:
    """Heuristic: does `url` likely point at a Swedish business resource?

    True if:
    - the registered hostname is in our curated allowlist, OR
    - the hostname's effective TLD is ``.se``.

    Returns False on malformed URLs (won't raise — scraping code is full of
    user-supplied strings).
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return False
    if not host:
        return False
    if host in _KNOWN_SWEDISH_BUSINESS_HOSTS:
        return True
    # Strip leading `www.` and compare on suffix
    stripped = host.removeprefix("www.")
    if stripped in _KNOWN_SWEDISH_BUSINESS_HOSTS:
        return True
    return host.endswith(".se")


# ─────────────────────────────────────────────────────────────────────────────
# Bezier mouse paths — humans don't fly in straight lines
# ─────────────────────────────────────────────────────────────────────────────
#
# A real mouse moves along a curved trajectory with micro-jitter. Headless
# anti-bot heuristics flag perfectly straight or instantaneous moves. The
# quadratic Bezier with a random control point gives the curvature; per-point
# Gaussian jitter gives the noise.

_BEZIER_JITTER_PX: float = 1.5
_BEZIER_CTRL_OFFSET_RATIO: float = 0.35


def bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int = 40,
) -> list[tuple[float, float]]:
    """Quadratic Bezier path between two points with random control + jitter.

    Parameters
    ----------
    start, end:
        ``(x, y)`` pixel coordinates.
    steps:
        Number of sampled points along the curve (inclusive of start/end).
        Typical values for "human" movements: 30-60. Clamped to ``>= 2``.

    Returns
    -------
    A list of ``(x, y)`` floats of length ``max(2, steps)``. The first
    point equals ``start`` and the last equals ``end`` exactly; interior
    points carry Gaussian jitter (~1.5px) on top of the Bezier curve.
    """
    steps = max(2, int(steps))
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])

    # Midpoint, then push the control point perpendicular to the start-end
    # line by a random fraction of the segment length. That makes the arc
    # bow naturally rather than along the axis-aligned midpoint.
    mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
    dx, dy = ex - sx, ey - sy
    seg_len = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector
    px, py = -dy / seg_len, dx / seg_len
    offset = random.uniform(-1.0, 1.0) * seg_len * _BEZIER_CTRL_OFFSET_RATIO
    cx, cy = mx + px * offset, my + py * offset

    points: list[tuple[float, float]] = []
    for i in range(steps):
        t = i / (steps - 1)
        # Quadratic Bezier: B(t) = (1-t)^2 P0 + 2(1-t)t P1 + t^2 P2
        one_minus_t = 1.0 - t
        bx = (one_minus_t * one_minus_t) * sx + 2 * one_minus_t * t * cx + t * t * ex
        by = (one_minus_t * one_minus_t) * sy + 2 * one_minus_t * t * cy + t * t * ey
        # Jitter — skip on endpoints so we land exactly on target.
        if 0 < i < steps - 1:
            bx += random.gauss(0.0, _BEZIER_JITTER_PX)
            by += random.gauss(0.0, _BEZIER_JITTER_PX)
        points.append((bx, by))

    # Force exact endpoints — Bezier already does this for t=0 and t=1
    # (without jitter) but make it explicit so callers can rely on it.
    points[0] = (sx, sy)
    points[-1] = (ex, ey)
    return points


# ─────────────────────────────────────────────────────────────────────────────
# Typing cadence — variable per-character delays + punctuation pauses
# ─────────────────────────────────────────────────────────────────────────────

_TYPING_PUNCT_PAUSE_CHARS: frozenset[str] = frozenset({".", ",", ";", ":", "!"})
_TYPING_BASE_MIN_MS: float = 80.0
_TYPING_BASE_MAX_MS: float = 180.0
_TYPING_PUNCT_PAUSE_MIN_MS: float = 200.0
_TYPING_PUNCT_PAUSE_MAX_MS: float = 400.0


def typing_cadence(text: str) -> list[float]:
    """Per-character delays (in seconds) for human-like typing.

    Each character gets a uniform 80-180ms delay; punctuation characters in
    ``. , ; : !`` additionally receive a +200-400ms pause to mimic the natural
    micro-rest at sentence/clause boundaries.

    Returns an empty list for empty input. The returned values are seconds,
    not milliseconds — feed them straight into ``asyncio.sleep`` or
    ``page.keyboard.type(delay=...)`` (the latter wants ms, so multiply by
    1000 in that case).
    """
    if not text:
        return []
    delays: list[float] = []
    for ch in text:
        d_ms = random.uniform(_TYPING_BASE_MIN_MS, _TYPING_BASE_MAX_MS)
        if ch in _TYPING_PUNCT_PAUSE_CHARS:
            d_ms += random.uniform(
                _TYPING_PUNCT_PAUSE_MIN_MS, _TYPING_PUNCT_PAUSE_MAX_MS
            )
        delays.append(d_ms / 1000.0)
    return delays


# ─────────────────────────────────────────────────────────────────────────────
# Realistic viewport sizes — weighted by real-world web usage
# ─────────────────────────────────────────────────────────────────────────────
#
# Hardcoded 1366x768 from older T4 versions was itself a fingerprint:
# unusually common in headless setups, less common on real machines today.
# Distribution mirrors statcounter desktop screen resolutions 2024-2025
# (rounded; primary buckets only).

_VIEWPORTS_WEIGHTED: tuple[tuple[tuple[int, int], int], ...] = (
    ((1920, 1080), 40),  # most common modern desktop / FHD
    ((1366, 768), 25),   # legacy laptops, still very common in EU corporates
    ((1536, 864), 20),   # 1.25 DPI scale of 1920x1080 (Windows default)
    ((1440, 900), 15),   # MacBook Air / Pro 13"
)


def realistic_viewport() -> tuple[int, int]:
    """Return a (width, height) viewport drawn from realistic web stats."""
    population, weights = zip(*_VIEWPORTS_WEIGHTED, strict=True)
    return random.choices(population, weights=weights, k=1)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Accept-Language header builder
# ─────────────────────────────────────────────────────────────────────────────


_ACCEPT_LANGUAGE_BY_COUNTRY: dict[str, str] = {
    "SE": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
    "NO": "nb-NO,nb;q=0.9,no;q=0.8,en-US;q=0.7,en;q=0.6",
    "DK": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
    "FI": "fi-FI,fi;q=0.9,sv;q=0.8,en-US;q=0.7,en;q=0.6",
    "IT": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "FR": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "GB": "en-GB,en;q=0.9,en-US;q=0.8",
    "US": "en-US,en;q=0.9",
}


def realistic_accept_language(country: str = "SE") -> str:
    """Return an Accept-Language header value for ``country`` (ISO-3166 alpha-2).

    Defaults to Swedish (``"sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7"``). Unknown
    country codes fall back to the Swedish default — this is the savantmedia
    use case, so that's the safer guess than English.
    """
    return _ACCEPT_LANGUAGE_BY_COUNTRY.get(
        country.upper(), _ACCEPT_LANGUAGE_BY_COUNTRY["SE"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Realistic scroll patterns — humans don't scroll like robots
# ─────────────────────────────────────────────────────────────────────────────
#
# A real reader scrolls in chunks (~200-500px), pauses to read (~0.5-2s),
# occasionally backtracks (scroll-up) when they overshoot, and eventually
# reaches the bottom. The previous implementation did 2-4 fixed scrolls
# with fixed deltas — that's itself a fingerprint.

_SCROLL_STEP_MIN: int = 200
_SCROLL_STEP_MAX: int = 500
_SCROLL_PAUSE_MIN: float = 0.4
_SCROLL_PAUSE_MAX: float = 1.8
_SCROLL_BACKTRACK_PROBABILITY: float = 0.15
_SCROLL_BACKTRACK_FRACTION: float = 0.35  # backtrack 35% of the last step


def realistic_scroll_pattern(page_height: int) -> list[tuple[int, float]]:
    """Return a sequence of ``(scroll_y, delay_seconds)`` simulating a human read.

    The list starts at ``y=0`` and ends at (or near) ``page_height`` so the
    caller can iterate ``page.evaluate(f"window.scrollTo(0, {y})")`` followed
    by ``asyncio.sleep(delay)``. Includes occasional backtrack steps with
    ~15% probability — typical "wait, what did that say" reader behaviour.

    For ``page_height <= 0`` returns an empty list (nothing to scroll).
    """
    if page_height <= 0:
        return []

    plan: list[tuple[int, float]] = []
    y = 0
    last_step: int = 0
    safety_max_iterations = 200  # never loop forever even on absurd heights

    while y < page_height and len(plan) < safety_max_iterations:
        step = random.randint(_SCROLL_STEP_MIN, _SCROLL_STEP_MAX)
        y = min(page_height, y + step)
        delay = random.uniform(_SCROLL_PAUSE_MIN, _SCROLL_PAUSE_MAX)
        plan.append((y, delay))
        last_step = step

        # Occasional backtrack — but never below 0 and never on the very
        # first scroll (no content above start to look at).
        if (
            len(plan) > 1
            and y < page_height
            and random.random() < _SCROLL_BACKTRACK_PROBABILITY
        ):
            back = int(last_step * _SCROLL_BACKTRACK_FRACTION)
            y = max(0, y - back)
            plan.append(
                (y, random.uniform(_SCROLL_PAUSE_MIN, _SCROLL_PAUSE_MAX))
            )

    # Ensure the last entry is exactly at page bottom — many lazy-load
    # triggers fire only on scroll-to-bottom.
    if not plan or plan[-1][0] < page_height:
        plan.append((page_height, random.uniform(_SCROLL_PAUSE_MIN, _SCROLL_PAUSE_MAX)))
    return plan
