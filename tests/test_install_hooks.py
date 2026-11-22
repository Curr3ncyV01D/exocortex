"""Check safe and repeatable hook installation without changing real Git hooks."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from scripts import install_hooks


def git_without_custom_hooks(command, **kwargs):
    if command[1:3] == ["config", "--get"]:
        return subprocess.CompletedProcess(command, 1, b"")
    return subprocess.CompletedProcess(command, 0, b".git/hooks/pre-commit\n")


def test_installation_is_idempotent_and_preserves_posix_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_hooks.subprocess, "run", git_without_custom_hooks)
    hook = install_hooks.install(tmp_path)
    assert hook == tmp_path / ".git/hooks/pre-commit"
    original = hook.read_bytes()
    assert original.startswith(b"#!/bin/sh\n") and b"\r" not in original
    assert b"./.venv/Scripts/python.exe" in original
    assert b"./.venv/bin/python" in original
    assert b'scripts/check.py --staged' in original
    assert install_hooks.install(tmp_path).read_bytes() == original
    if os.name != "nt":
        assert hook.stat().st_mode & stat.S_IXUSR


def test_existing_user_hook_is_not_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_hooks.subprocess, "run", git_without_custom_hooks)
    hook = tmp_path / ".git/hooks/pre-commit"
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b"#!/bin/sh\necho user-hook\n")
    with pytest.raises(RuntimeError, match="Existing pre-commit differs"):
        install_hooks.install(tmp_path)
    assert hook.read_bytes() == b"#!/bin/sh\necho user-hook\n"


def test_custom_hook_directory_is_not_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_hooks.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, b"/user/hooks\n",
    ))
    with pytest.raises(RuntimeError, match="core.hooksPath"):
        install_hooks.install(tmp_path)
    assert not (tmp_path / ".git").exists()


def test_git_failure_does_not_create_a_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(install_hooks.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        install_hooks.install(tmp_path)
    assert not (tmp_path / ".git").exists()
