"""
Obsidian vault MD writer — Fase 11 (`docs/ARCHITECTURE.md` §7).

**Write boundary (hard constraint)**
------------------------------------
The agent is allowed to write **only** inside
`{VAULT_PATH}/Workflows/scraping-runs/`. Every other vault note is
read-only: any code path that ever writes outside this directory is
a bug.

This module enforces the boundary by:
1. Resolving the target absolute path inside `workflows_dir()` and
   refusing to write if the path escapes it.
2. Never offering an "overwrite" path: if the target file already
   exists, a numeric suffix (`-2`, `-3`, ...) is appended until a
   free slot is found.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("savantsdatabas.memory.vault_writer")


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_VAULT_PATH = "C:/Users/loren/Desktop/lorenzovault"
_WORKFLOWS_SUBDIR = ("Workflows", "scraping-runs")


def vault_path() -> Path:
    """Return the absolute vault root, validated to exist.

    Reads `VAULT_PATH` from env (falls back to the Lorenzo dev default).
    Raises `FileNotFoundError` if the directory is missing — caller may
    catch this to degrade gracefully (e.g. skip vault write but still
    ingest into pgvector).
    """
    raw = os.environ.get("VAULT_PATH", _DEFAULT_VAULT_PATH)
    p = Path(raw).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"VAULT_PATH does not exist or is not a directory: {p}")
    return p


def workflows_dir() -> Path:
    """Return `{vault}/Workflows/scraping-runs/`, creating it if missing.

    `mkdir(parents=True, exist_ok=True)` is safe: it only creates the
    `scraping-runs` leaf under an already-existing vault root.
    """
    d = vault_path().joinpath(*_WORKFLOWS_SUBDIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Slugify (Lorenzo's vault style: lowercase, dashes, ascii-only, max 60 chars)
# ─────────────────────────────────────────────────────────────────────────────

_DIACRITICS = str.maketrans(
    "àáâãäåèéêëìíîïòóôõöùúûüýÿñçÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÝŸÑÇ",
    "aaaaaaeeeeiiiiooooouuuuyyncAAAAAAEEEEIIIIOOOOOUUUUYYNC",
)


def slugify(s: str) -> str:
    """Lowercase, ascii-only, dash-separated, max 60 chars.

    Strips emojis, Swedish chars are normalised to ASCII fallbacks
    (å→a, ö→o, ä→a). Multiple separators collapse to a single dash.
    Returns `"untitled"` if the input reduces to an empty string.
    """
    if not s:
        return "untitled"
    # Light unicode normalisation
    s = s.translate(_DIACRITICS)
    # Swedish-specific
    s = s.replace("å", "a").replace("ä", "a").replace("ö", "o")
    s = s.replace("Å", "a").replace("Ä", "a").replace("Ö", "o")
    # Lowercase + replace any non [a-z0-9] run with a dash
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        return "untitled"
    return s[:60].rstrip("-") or "untitled"


# ─────────────────────────────────────────────────────────────────────────────
# Path safety
# ─────────────────────────────────────────────────────────────────────────────


def _safe_target(filename: str) -> Path:
    """Resolve `workflows_dir()/filename` and assert it stays inside the dir.

    Defends against accidental `..` in slugs (slugify already strips
    them, but belt-and-braces).
    """
    base = workflows_dir().resolve()
    candidate = (base / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to write outside workflows_dir: {candidate} (base={base})"
        ) from exc
    return candidate


def _unique_path(target: Path) -> Path:
    """Append `-2`, `-3`, ... to `target.stem` until a free path is found."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ─────────────────────────────────────────────────────────────────────────────
# Frontmatter + body composition
# ─────────────────────────────────────────────────────────────────────────────


def _yaml_escape(s: str) -> str:
    """Escape a string for safe inclusion as a YAML double-quoted scalar."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _build_frontmatter(*, run_id: str, user_prompt: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    short_prompt = _yaml_escape(user_prompt or "")[:200]
    lines = [
        "---",
        "tags: [workflow, scraping-run, status/auto-generated]",
        f"created: {today}",
        f'run_id: "{_yaml_escape(run_id)}"',
        f'user_prompt: "{short_prompt}"',
        "source: savantsdatabas-agent",
        "---",
        "",
    ]
    return "\n".join(lines)


def _build_body(
    *,
    user_prompt: str,
    plan_steps: list[dict],
    results: dict,
    lessons: list[str],
) -> str:
    parts: list[str] = []

    parts.append("# Prompt\n")
    parts.append((user_prompt or "_(empty prompt)_").strip())
    parts.append("")

    parts.append("## Plan steps")
    if not plan_steps:
        parts.append("_(no steps)_")
    else:
        for i, step in enumerate(plan_steps, start=1):
            sid = step.get("id", f"s{i}")
            src = step.get("source", "?")
            tier = step.get("tier", "?")
            q = (step.get("query") or "").strip()
            ey = (step.get("expected_yield") or "").strip()
            rat = (step.get("rationale") or "").strip()
            parts.append(f"{i}. **[{sid}]** `{src}` (T{tier}) — {q}")
            if ey:
                parts.append(f"   - expected: {ey}")
            if rat:
                parts.append(f"   - rationale: {rat}")
    parts.append("")

    parts.append("## Results")
    if not results:
        parts.append("_(no results captured)_")
    else:
        # Render dict as a simple bulleted key/value list (no fragile tables).
        for k, v in results.items():
            parts.append(f"- **{k}**: {v}")
    parts.append("")

    parts.append("## Lessons learned")
    if not lessons:
        parts.append("_(no lessons recorded)_")
    else:
        for line in lessons:
            line = (line or "").strip()
            if line:
                parts.append(f"- {line}")
    parts.append("")

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def write_run_note(
    *,
    run_id: str,
    user_prompt: str,
    plan_steps: list[dict],
    results: dict,
    lessons: list[str],
) -> Path:
    """Write a scraping-run note as `YYYY-MM-DD-<slug>.md`.

    Never overwrites: if the target exists, appends `-2`, `-3`, … until
    a free slot is found.

    Returns the absolute path of the file actually written.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = slugify(user_prompt or run_id or "run")
    filename = f"{today}-{slug}.md"

    target = _safe_target(filename)
    target = _unique_path(target)

    body = _build_frontmatter(run_id=run_id, user_prompt=user_prompt) + _build_body(
        user_prompt=user_prompt,
        plan_steps=plan_steps,
        results=results,
        lessons=lessons,
    )

    target.write_text(body, encoding="utf-8")
    log.info("write_run_note: wrote %s (%d bytes)", target, len(body))
    return target
