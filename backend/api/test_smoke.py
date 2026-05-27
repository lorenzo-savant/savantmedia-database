"""
Smoke test for the FastAPI orchestrator endpoints.

Run directly:
    cd backend
    .venv/Scripts/python.exe -m api.test_smoke

Uses fastapi.testclient.TestClient (in-process, no live server needed).
Prints a pass/fail summary per endpoint with status code + short body excerpt.

NOTE: Hits the real Supabase backend (via api.deps.get_supabase). If env vars
are missing, /db queries will fail — we still report that as a "soft" pass
because the *router* is wired correctly.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Load .env BEFORE importing the app (so verify_token / supabase see env vars).
from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

# Ensure `backend/` is on sys.path so `from api.main import app` works
# even when run as `python api/test_smoke.py`.
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


def _short(body: object, n: int = 200) -> str:
    try:
        s = json.dumps(body, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = str(body)
    return s if len(s) <= n else s[:n] + "…"


def _headers() -> dict[str, str]:
    tok = os.environ.get("ORCHESTRATOR_API_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def main() -> int:
    print("=" * 70)
    print("Savantsdatabas FastAPI smoke test")
    print("=" * 70)

    auth_mode = "bearer" if os.environ.get("ORCHESTRATOR_API_TOKEN") else "dev (no auth)"
    print(f"auth mode: {auth_mode}")
    print(f"SUPABASE_URL set: {bool(os.environ.get('SUPABASE_URL'))}")
    print(f"SUPABASE_SECRET_KEY set: {bool(os.environ.get('SUPABASE_SECRET_KEY'))}")
    print()

    # Show the routes the app actually exposes
    routes = sorted({getattr(r, "path", str(r)) for r in app.routes})
    print("Registered routes:")
    for p in routes:
        print(f"  - {p}")
    print()

    client = TestClient(app)
    results: list[tuple[str, int, str]] = []

    # ── 1) GET / ────────────────────────────────────────────────────────────
    try:
        r = client.get("/")
        results.append(("GET /", r.status_code, _short(r.json())))
    except Exception as exc:  # noqa: BLE001
        results.append(("GET /", -1, f"EXC: {exc}"))

    # ── 2) GET /orchestrator/plans?limit=5 ─────────────────────────────────
    try:
        r = client.get("/orchestrator/plans?limit=5", headers=_headers())
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        results.append(("GET /orchestrator/plans?limit=5", r.status_code, _short(body)))
    except Exception as exc:  # noqa: BLE001
        results.append(("GET /orchestrator/plans?limit=5", -1, f"EXC: {exc}"))

    # ── 3) POST /orchestrator/plan ─────────────────────────────────────────
    plan_id: str | None = None
    thread_id: str | None = None
    try:
        r = client.post(
            "/orchestrator/plan",
            json={"user_prompt": "smoke test: trovami IT-konsulter i Skåne"},
            headers=_headers(),
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        results.append(("POST /orchestrator/plan", r.status_code, _short(body)))
        if r.status_code == 200 and isinstance(body, dict):
            plan_id = body.get("plan_id")
            thread_id = body.get("thread_id")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results.append(("POST /orchestrator/plan", -1, f"EXC: {exc}"))

    # ── 4) GET /orchestrator/plans/{plan_id} ───────────────────────────────
    if plan_id:
        try:
            r = client.get(f"/orchestrator/plans/{plan_id}", headers=_headers())
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            results.append((f"GET /orchestrator/plans/{plan_id[:8]}…", r.status_code, _short(body)))
        except Exception as exc:  # noqa: BLE001
            results.append((f"GET /orchestrator/plans/{plan_id[:8]}…", -1, f"EXC: {exc}"))

        # ── 5) POST /orchestrator/plans/{plan_id}/approve ──────────────────
        try:
            r = client.post(
                f"/orchestrator/plans/{plan_id}/approve",
                json={"approved_step_ids": ["s1", "s2"], "thread_id": thread_id},
                headers=_headers(),
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            results.append((f"POST /orchestrator/plans/{plan_id[:8]}…/approve", r.status_code, _short(body)))
        except Exception as exc:  # noqa: BLE001
            results.append(
                (f"POST /orchestrator/plans/{plan_id[:8]}…/approve", -1, f"EXC: {exc}")
            )

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Results")
    print("=" * 70)
    ok = 0
    bad = 0
    for name, code, body in results:
        marker = "OK " if 200 <= code < 300 else "FAIL"
        if 200 <= code < 300:
            ok += 1
        else:
            bad += 1
        print(f"[{marker}] {code:>4}  {name}")
        print(f"        {body}")

    print()
    print(f"PASS: {ok}   FAIL: {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
