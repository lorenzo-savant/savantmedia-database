"""
FastAPI dependencies for the Savantsdatabas Orchestrator API.

Centralizes:
- Supabase client (cached, service-role secret key)
- Bearer token auth (opt-in via ORCHESTRATOR_API_TOKEN env var)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from fastapi import Header, HTTPException, status
from supabase import Client, create_client

log = logging.getLogger("savantsdatabas.api.deps")


# ─────────────────────────────────────────────────────────────────────────────
# Supabase client (singleton)
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Return a cached Supabase client.

    Uses SUPABASE_URL + SUPABASE_SECRET_KEY (service role) from env.
    Raises RuntimeError if either is missing.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set in environment "
            "(check backend/.env)."
        )
    log.debug("Creating Supabase client for %s", url)
    return create_client(url, key)


# ─────────────────────────────────────────────────────────────────────────────
# Auth: optional bearer token
# ─────────────────────────────────────────────────────────────────────────────


def verify_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Validate the `Authorization: Bearer <token>` header if a token is configured.

    If `ORCHESTRATOR_API_TOKEN` is unset, this is a no-op (dev mode).
    If set, the header must match exactly.
    """
    expected = os.environ.get("ORCHESTRATOR_API_TOKEN")
    if not expected:
        # Dev mode: no auth required. Warning is logged once at app startup.
        return

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>').",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
