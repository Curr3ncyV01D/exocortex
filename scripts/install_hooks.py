"""Install the repository's local verification hook without replacing user hooks."""

import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = """#!/bin/sh
# ExoCortex managed pre-commit verification.
set -eu
cd "$(git rev-parse --show-toplevel)"
if [ -x "./.venv/Scripts/python.exe" ]; then
    python="./.venv/Scripts/python.exe"
elif [ -x "./.venv/bin/python" ]; then
    python="./.venv/bin/python"
else
    echo "[pre-commit] Project .venv is missing; create it and install requirements.txt." >&2
    exit 1
fi
echo "[pre-commit] Running staged checks."
exec "$python" scripts/check.py --staged
"""


def install(root: Path) -> Path:
    """Create an executable LF-only hook, or accept an identical installation."""
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=root, stdout=subprocess.PIPE, check=False,
    )
    if configured.returncode not in (0, 1):
        raise RuntimeError("Cannot read Git hook configuration.")
    if configured.returncode == 0:
        raise RuntimeError("Custom core.hooksPath is configured; refusing to change user hooks.")
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks/pre-commit"],
        cwd=root, check=True, stdout=subprocess.PIPE,
    )
    hook = Path(result.stdout.decode().strip())
    if not hook.is_absolute():
        hook = root / hook
    payload = HOOK.encode("utf-8")
    if hook.is_symlink():
        raise RuntimeError("Existing pre-commit is a symlink; refusing to replace it.")
    if hook.exists() and hook.read_bytes() != payload:
        raise RuntimeError("Existing pre-commit differs; review it before installing this hook.")
    hook.parent.mkdir(parents=True, exist_ok=True)
    if not hook.exists():
        hook.write_bytes(payload)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


def main() -> int:
    """Report installation errors without modifying Git configuration."""
    try:
        print(f"Installed pre-commit hook: {install(ROOT)}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[install-hooks] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
