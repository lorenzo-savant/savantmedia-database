"""
Fallback chain: Python primary → Node.js fallback per email discovery.

Vault regola Lorenzo (2026-06-02):
    "Se Python fallisce, prova Node. Costo zero. L'obiettivo è trovare i dati,
    non difendere un linguaggio."

Chain di chiamate:
1. **Python primary**: `email_search.find_emails_on_domain()` /
   `find_emails_for_person()` (httpx+BS4 contro Brave/Ecosia/Bing).
2. **Node.js fallback**: chiamata subprocess a `scrapers-node/find_emails.js`
   o `find_dm_email.js` quando Python:
   - ritorna `set()` vuoto (= rate-limited o nessun hit)
   - OR solleva eccezioni di rete consecutive

Il fallback è opt-in tramite env var:
    EMAIL_FALLBACK_NODE=1

Senza env var, il fallback è disabilitato (Python-only mode).
Setup Node:
    cd backend/scrapers-node && npm install

Usage:
    from scrapers.email_fallback import find_emails_chain
    emails = await find_emails_chain(domain="savantmedia.se")
    # → set of strings, Python first, Node fallback se vuoto + env attivo
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .email_search import find_emails_on_domain, find_emails_for_person

logger = logging.getLogger(__name__)

_NODE_DIR = Path(__file__).resolve().parent.parent / "scrapers-node"
_FALLBACK_ENABLED_DEFAULT = bool(int(os.environ.get("EMAIL_FALLBACK_NODE", "0")))


async def _run_node(script: str, *args: str, timeout: float = 60.0) -> set[str]:
    """Spawn node.js script as subprocess, parse JSON stdout."""
    if not _NODE_DIR.exists():
        logger.debug("Node fallback dir not found: %s", _NODE_DIR)
        return set()
    script_path = _NODE_DIR / script
    if not script_path.exists():
        logger.debug("Node script not found: %s", script_path)
        return set()
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(script_path),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_NODE_DIR),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("Node fallback %s timeout %ds", script, timeout)
            return set()
        if proc.returncode != 0:
            logger.debug(
                "Node %s exit %d stderr=%s",
                script, proc.returncode,
                (stderr_b or b"").decode("utf-8", errors="replace")[:300],
            )
            return set()
        try:
            data = json.loads(stdout_b.decode("utf-8", errors="replace"))
            if isinstance(data, list):
                return {str(x).strip().lower() for x in data if isinstance(x, str)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return set()
    except FileNotFoundError:
        logger.debug("Node binary not found in PATH")
        return set()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Node fallback %s: %s", script, exc)
        return set()
    return set()


async def find_emails_chain(
    domain: str,
    person_name: str | None = None,
    enable_node_fallback: bool | None = None,
) -> set[str]:
    """Chain: Python first, Node fallback if Python returns empty.

    If `person_name` provided → search specific to that person.
    Otherwise → all emails on `domain`.
    """
    enable_fb = (
        enable_node_fallback
        if enable_node_fallback is not None
        else _FALLBACK_ENABLED_DEFAULT
    )

    # Python primary
    if person_name:
        py_emails = await find_emails_for_person(person_name, domain)
    else:
        py_emails = await find_emails_on_domain(domain)

    if py_emails:
        return py_emails

    if not enable_fb:
        return py_emails  # empty

    # Node fallback
    if person_name:
        node_emails = await _run_node("find_dm_email.js", person_name, domain)
    else:
        node_emails = await _run_node("find_emails.js", domain)

    return py_emails | node_emails
