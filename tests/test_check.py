"""Verify the local checker contract without running nested test suites."""

import subprocess
from pathlib import Path

import pytest

from scripts import check


@pytest.mark.parametrize(("paths", "expected"), [
    ([], ()),
    (["README.md", "docs/assets/screenshot.png"], ()),
    (["frontend/README.md", "src/notes.md"], ()),
    (["src/services.py"], ("pytest", "alembic")),
    (["tests/test_graph.py"], ("pytest", "alembic")),
    (["migrations/versions/0001.py"], ("pytest", "alembic")),
    (["scripts/install_hooks.py"], ("pytest", "alembic")),
    (["requirements-dev.txt"], ("pytest", "alembic")),
    (["constraints.txt", "pytest.ini", "alembic.ini"], ("pytest", "alembic")),
    (["frontend/src/App.tsx"], ("typecheck", "frontend-test")),
    (["src/services.py", "frontend/src/App.tsx"], ("pytest", "alembic", "typecheck", "frontend-test")),
    (["src/services.py", "frontend/README.md"], ("pytest", "alembic")),
    (["scripts/check.py"], check.BASE),
    ([".github/workflows/ci.yml"], check.BASE),
    (["Dockerfile"], check.BASE),
])
def test_staged_checks_follow_the_path_contract(paths: list[str], expected: tuple[str, ...]) -> None:
    assert check.staged_checks(paths) == expected


def test_staged_paths_preserve_names_and_include_deleted_rename_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend-to-docs rename must still trigger backend verification."""
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"src/old name.py\0docs/new name.md\0")

    monkeypatch.setattr(check.subprocess, "run", run)
    paths = check.staged_paths(tmp_path)
    assert paths == ["src/old name.py", "docs/new name.md"]
    assert "--no-renames" in calls[0][0] and "-z" in calls[0][0]
    assert calls[0][1]["cwd"] == tmp_path
    assert check.staged_checks(paths) == ("pytest", "alembic")


@pytest.mark.parametrize(("args", "expected"), [([], check.BASE), (["--e2e"], (*check.BASE, "e2e"))])
def test_main_dispatches_the_requested_mode(
    args: list[str], expected: tuple[str, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(check, "run_checks", lambda checks, root: calls.append((checks, root)) or 0)
    assert check.main(args) == 0
    assert calls == [(expected, check.ROOT)]


def test_documentation_only_staging_does_not_resolve_or_run_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "staged_paths", lambda root: ["AGENTS.md", "docs/how-to/checks.md"])

    def unexpected(*args):
        pytest.fail("Documentation-only staging must not run checks.")

    monkeypatch.setattr(check, "run_checks", unexpected)
    assert check.main(["--staged"]) == 0


def test_main_reports_git_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(root):
        raise subprocess.CalledProcessError(128, ["git", "diff"])

    monkeypatch.setattr(check, "staged_paths", fail)
    assert check.main(["--staged"]) == 1


def test_conflicting_modes_are_rejected() -> None:
    with pytest.raises(SystemExit) as error:
        check.main(["--e2e", "--staged"])
    assert error.value.code == 2


@pytest.mark.parametrize("windows", [False, True])
def test_node_commands_use_platform_wrappers_and_frontend_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool,
) -> None:
    monkeypatch.setattr(check, "WINDOWS", windows)
    monkeypatch.setattr(check.shutil, "which", lambda name: f"/tools/{name}")
    suffix = ".cmd" if windows else ""
    for name, executable, arguments in [
        ("frontend-test", "npm", ["test"]),
        ("build", "npm", ["run", "build"]),
        ("typecheck", "npx", ["tsc", "-b"]),
        ("e2e", "npm", ["run", "test:e2e"]),
    ]:
        command, cwd = check.command_for(name, tmp_path)
        assert command == [f"/tools/{executable}{suffix}", *arguments]
        assert cwd == tmp_path / "frontend"


def test_backend_commands_use_the_project_environment(tmp_path: Path) -> None:
    python = tmp_path / ".venv" / ("Scripts/python.exe" if check.WINDOWS else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    for name, arguments in [
        ("pytest", ["-m", "pytest"]),
        ("alembic", ["-m", "alembic", "check"]),
        ("pip", ["-m", "pip", "check"]),
    ]:
        command, cwd = check.command_for(name, tmp_path)
        assert command == [str(python), *arguments] and cwd == tmp_path


def test_missing_python_environment_is_an_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=".venv"):
        check.command_for("pytest", tmp_path)


@pytest.mark.parametrize("windows", [False, True])
def test_run_checks_stops_at_failure_and_wraps_only_windows_batch_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool,
) -> None:
    calls = []
    command = ["npm.cmd" if windows else "npm", "test"]
    monkeypatch.setattr(check, "WINDOWS", windows)
    monkeypatch.setattr(check, "command_for", lambda name, root: (command, root / "frontend"))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(check.subprocess, "run", run)
    assert check.run_checks(["frontend-test", "build"], tmp_path) == 7
    assert len(calls) == 1
    assert calls[0][1]["shell"] is windows
    assert calls[0][1]["cwd"] == tmp_path / "frontend"
