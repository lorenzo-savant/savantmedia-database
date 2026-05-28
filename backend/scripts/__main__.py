"""
CLI router for backend/scripts/ enrichment commands.

Usage:
    python -m scripts                  # equivalent to `list`
    python -m scripts list             # show all available scripts + first docstring line
    python -m scripts <name> [args]    # delegate to scripts.<name> preserving argv
    python -m scripts --help           # show this usage

Exit codes:
    0  success (also propagated from delegated script)
    2  unknown script name

Discovery is via pkgutil.iter_modules on this package's __path__, excluding
__main__ and dunder/private modules. Docstring summary is extracted via
ast.parse to avoid importing target modules (which may initialize Supabase
clients or read env at top level).
"""

from __future__ import annotations

import ast
import pkgutil
import subprocess
import sys
from pathlib import Path

_USAGE = (
    "usage: python -m scripts [list | <script_name> [args...] | --help]\n"
    "  list                    enumerate available scripts with summary\n"
    "  <script_name> [args]    run scripts.<script_name> with the remaining args\n"
    "  --help, -h              show this message\n"
)


def _iter_script_modules() -> list[str]:
    """Return sorted list of public sibling module names (no __main__, no _private)."""
    pkg_path = [str(Path(__file__).parent)]
    names = [
        m.name
        for m in pkgutil.iter_modules(pkg_path)
        if not m.ispkg and m.name != "__main__" and not m.name.startswith("_")
    ]
    return sorted(names)


def _docstring_summary(module_name: str) -> str:
    """Extract first non-empty line of module docstring via ast (no import)."""
    path = Path(__file__).parent / f"{module_name}.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return "(docstring unavailable)"
    if not doc:
        return "(no docstring)"
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "(empty docstring)"


def _print_list() -> int:
    modules = _iter_script_modules()
    if not modules:
        print("(no scripts found)")
        return 0
    width = max(len(m) for m in modules)
    for name in modules:
        print(f"  {name.ljust(width)}  {_docstring_summary(name)}")
    return 0


def _delegate(name: str, rest: list[str]) -> int:
    cmd = [sys.executable, "-m", f"scripts.{name}", *rest]
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] == "list":
        return _print_list()

    if args[0] in ("--help", "-h"):
        print(_USAGE, end="")
        return 0

    name, rest = args[0], args[1:]
    available = _iter_script_modules()
    if name not in available:
        print(f"unknown script: {name}", file=sys.stderr)
        print(f"available: {', '.join(available)}", file=sys.stderr)
        return 2

    return _delegate(name, rest)


if __name__ == "__main__":
    raise SystemExit(main())
