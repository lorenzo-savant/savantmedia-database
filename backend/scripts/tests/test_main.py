"""Smoke tests for the scripts.__main__ CLI router (stdlib unittest, zero deps)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

KNOWN_SCRIPTS = (
    "apply_subagent_results",
    "enrich_companies",
    "enrich_existing",
    "enrich_ratsit_t4",
    "enrich_sni_from_scb",
    "import_bolagsverket_bulk",
    "prepare_subagent_batches",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "scripts", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class RouterSmokeTests(unittest.TestCase):
    def test_default_invocation_lists(self) -> None:
        """`python -m scripts` with no args defaults to list mode."""
        result = _run()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for name in KNOWN_SCRIPTS:
            self.assertIn(name, result.stdout, f"{name} missing from default output")

    def test_list_enumerates_known_scripts(self) -> None:
        result = _run("list")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for name in KNOWN_SCRIPTS:
            self.assertIn(name, result.stdout, f"{name} missing from list output")
        self.assertNotIn("__main__", result.stdout)

    def test_unknown_script_exits_2(self) -> None:
        result = _run("bogus_script_xyz_does_not_exist")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown script", result.stderr.lower())

    def test_help_passthrough(self) -> None:
        """Delegating to a known script with --help must return its argparse usage."""
        result = _run("import_bolagsverket_bulk", "--help")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
