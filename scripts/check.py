"""Run project verification using the repository virtual environment."""

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
BASE = ("pytest", "alembic", "pip", "frontend-test", "build")


def staged_checks(paths: Sequence[str]) -> tuple[str, ...]:
    """Select checks conservatively without depending on staged file contents."""
    relevant = [path for path in paths if not (path.endswith(".md") or path.startswith("docs/"))]
    if not relevant:
        return ()
    if "scripts/check.py" in relevant:
        return BASE
    backend = False
    frontend = False
    for path in relevant:
        if path.startswith(("src/", "tests/", "migrations/", "scripts/")) or (
            "/" not in path and PurePosixPath(path).match("requirements*.txt")
        ) or path in {"constraints.txt", "pytest.ini", "alembic.ini"}:
            backend = True
        elif path.startswith("frontend/"):
            frontend = True
        else:
            return BASE
    return (("pytest", "alembic") if backend else ()) + (
        ("typecheck", "frontend-test") if frontend else ()
    )


def staged_paths(root: Path) -> list[str]:
    """Read both sides of renames and preserve spaces and non-ASCII names."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--no-renames", "-z"],
        cwd=root, check=True, stdout=subprocess.PIPE,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def command_for(check: str, root: Path) -> tuple[list[str], Path]:
    """Resolve tools only for checks that will actually run."""
    python = root / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if check in {"pytest", "alembic", "pip"}:
        if not python.is_file():
            raise FileNotFoundError("Project .venv is missing. Create it and install requirements.txt.")
        arguments = {
            "pytest": ["-m", "pytest"],
            "alembic": ["-m", "alembic", "check"],
            "pip": ["-m", "pip", "check"],
        }
        return [str(python), *arguments[check]], root
    name = "npx" if check == "typecheck" else "npm"
    executable = shutil.which(name + (".cmd" if WINDOWS else ""))
    if executable is None:
        raise FileNotFoundError(f"{name} is missing. Install Node.js 24 and run npm ci in frontend/.")
    arguments = {
        "typecheck": ["tsc", "-b"],
        "frontend-test": ["test"],
        "build": ["run", "build"],
        "e2e": ["run", "test:e2e"],
    }
    return [executable, *arguments[check]], root / "frontend"


def run_checks(checks: Sequence[str], root: Path) -> int:
    """Stop at the first failure and preserve the failing command's exit code."""
    for check in checks:
        command, cwd = command_for(check, root)
        print(f"[check] {check}: {' '.join(command)}", flush=True)
        # Python quotes the resolved Windows batch wrapper; arguments are fixed above.
        result = subprocess.run(
            command, cwd=cwd, check=False,
            shell=WINDOWS and command[0].lower().endswith((".cmd", ".bat")),
        )
        if result.returncode:
            print(f"[check] FAILED: {check} (exit {result.returncode})", file=sys.stderr, flush=True)
            return result.returncode if result.returncode > 0 else 1
    print("[check] All selected checks passed.", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the base, browser-inclusive or staged verification mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--e2e", action="store_true", help="Run base checks and browser acceptance tests.")
    mode.add_argument("--staged", action="store_true", help="Select quick checks from the Git index.")
    args = parser.parse_args(argv)
    try:
        checks = staged_checks(staged_paths(ROOT)) if args.staged else BASE + (("e2e",) if args.e2e else ())
        if not checks:
            print("[check] No code changes staged; skipping checks.", flush=True)
            return 0
        return run_checks(checks, ROOT)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[check] {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[check] Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
