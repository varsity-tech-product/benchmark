from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from orchestrator import container_manager as legacy_container_mod
from orchestrator.container_manager import ContainerManager as LegacyContainerManager
from server.core import container as container_mod
from server.core.container import ContainerManager
from server.core.staging import (
    cleanup_staged_dirs,
    create_staged_dirs,
    create_staged_sample_code,
)


def _mode(path: str | Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_staged_mount_dirs_are_world_searchable(tmp_path):
    data_src = tmp_path / "source_data"
    docs_src = tmp_path / "source_docs"
    code_src = tmp_path / "source_code"
    data_src.mkdir()
    docs_src.mkdir()
    code_src.mkdir()
    (data_src / "prices.csv").write_text("Date,Close\n2024-01-01,1\n", encoding="utf-8")
    (docs_src / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (code_src / "starter.py").write_text("print('ok')\n", encoding="utf-8")

    staged_data, staged_docs, temp_dirs = create_staged_dirs(
        ["prices.csv"],
        ["guide.md"],
        [str(data_src)],
        str(docs_src),
    )
    staged_code, code_temp_dirs = create_staged_sample_code(
        "starter.py",
        user_code_dir=str(code_src),
    )

    try:
        assert _mode(staged_data) == 0o755
        assert _mode(staged_docs) == 0o755
        assert staged_code is not None
        assert _mode(staged_code) == 0o755
    finally:
        cleanup_staged_dirs(temp_dirs + code_temp_dirs)


def test_create_container_chowns_workspace_once_for_fresh_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls: list[object] = []
    workspace = tmp_path / "workspace"

    def fake_mkdtemp(prefix: str) -> str:
        workspace.mkdir()
        return str(workspace)

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        stdout = "container123456789\n" if isinstance(cmd, str) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ContainerManager, "_docker_available", lambda self: True)
    monkeypatch.setattr(container_mod.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)

    manager = ContainerManager(docker_image="quant-bench-env:v3.0", use_docker=True)
    info = manager.create_container(
        "permtest",
        data_dir=str(tmp_path),
        docs_dir=str(tmp_path),
    )

    assert info.container_id == "container123"
    assert _mode(workspace) == 0o755
    chown_calls = [
        cmd
        for cmd in calls
        if isinstance(cmd, list)
        and cmd[:8]
        == [
            "docker",
            "exec",
            "--user",
            "root",
            "container123",
            "chown",
            "-R",
            "sandbox:sandbox",
        ]
    ]
    assert len(chown_calls) == 1


def test_local_fallback_workspace_stays_private():
    manager = ContainerManager(docker_image="quant-bench-env:v3.0", use_docker=False)
    info = manager.create_container(
        "localperm",
        data_dir="",
        docs_dir="",
    )
    try:
        assert _mode(info.workspace_path) == 0o700
    finally:
        manager.destroy_container(info.container_id)


def test_restore_workspace_host_ownership_restarts_stopped_container(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[object] = []
    restore_attempts = 0

    def fake_run(cmd, *args, **kwargs):
        nonlocal restore_attempts
        calls.append(cmd)
        if (
            isinstance(cmd, list)
            and cmd[:6] == ["docker", "exec", "--user", "root", "container123", "sh"]
        ):
            restore_attempts += 1
            return subprocess.CompletedProcess(
                cmd,
                0 if restore_attempts == 2 else 1,
                stdout="",
                stderr="container is not running",
            )
        if (
            isinstance(cmd, list)
            and cmd[:5] == ["docker", "inspect", "-f", "{{.State.Running}}", "container123"]
        ):
            return subprocess.CompletedProcess(cmd, 0, stdout="false\n", stderr="")
        if isinstance(cmd, list) and cmd[:3] == ["docker", "start", "container123"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)

    assert ContainerManager._restore_workspace_host_ownership("container123") is True
    assert ["docker", "start", "container123"] in calls
    assert restore_attempts == 2


def test_destroy_container_restores_host_ownership_before_docker_rm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls: list[object] = []
    workspace = tmp_path / "workspace"

    def fake_mkdtemp(prefix: str) -> str:
        workspace.mkdir()
        return str(workspace)

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        stdout = "container123456789\n" if isinstance(cmd, str) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ContainerManager, "_docker_available", lambda self: True)
    monkeypatch.setattr(container_mod.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)

    manager = ContainerManager(docker_image="quant-bench-env:v3.0", use_docker=True)
    info = manager.create_container(
        "cleanupperm",
        data_dir=str(tmp_path),
        docs_dir=str(tmp_path),
    )
    manager.destroy_container(info.container_id)

    restore_index = next(
        index
        for index, cmd in enumerate(calls)
        if isinstance(cmd, list)
        and cmd[:6] == ["docker", "exec", "--user", "root", "container123", "sh"]
    )
    rm_index = next(
        index
        for index, cmd in enumerate(calls)
        if isinstance(cmd, str) and cmd.startswith("docker rm -f container123")
    )

    assert restore_index < rm_index
    assert f"chown -R {os.getuid()}:{os.getgid()} /workspace" in calls[restore_index][-1]


def test_resume_restore_path_chowns_workspace_once_after_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls: list[object] = []
    workspace = tmp_path / "workspace"

    def fake_mkdtemp(prefix: str) -> str:
        workspace.mkdir()
        return str(workspace)

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        stdout = "containerabcdef12\n" if isinstance(cmd, str) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ContainerManager, "_docker_available", lambda self: True)
    monkeypatch.setattr(container_mod.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)

    manager = ContainerManager(docker_image="quant-bench-env:v3.0", use_docker=True)
    manager.create_container(
        "resumeperm",
        data_dir=str(tmp_path),
        docs_dir=str(tmp_path),
        restore_workspace_snapshot=True,
    )

    restore_calls = [
        cmd
        for cmd in calls
        if isinstance(cmd, list)
        and cmd[:6] == ["docker", "exec", "--user", "root", "containerabc", "bash"]
    ]
    assert restore_calls
    assert "chown -R" not in restore_calls[-1][-1]

    chown_calls = [
        cmd
        for cmd in calls
        if isinstance(cmd, list)
        and cmd[:8]
        == [
            "docker",
            "exec",
            "--user",
            "root",
            "containerabc",
            "chown",
            "-R",
            "sandbox:sandbox",
        ]
    ]
    assert len(chown_calls) == 1


def test_legacy_cli_container_chowns_workspace_for_fresh_docker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls: list[object] = []
    workspace = tmp_path / "workspace"

    def fake_mkdtemp(prefix: str) -> str:
        workspace.mkdir()
        return str(workspace)

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        stdout = "legacycontainer12\n" if isinstance(cmd, str) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(LegacyContainerManager, "_docker_available", lambda self: True)
    monkeypatch.setattr(legacy_container_mod.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(legacy_container_mod.subprocess, "run", fake_run)

    manager = LegacyContainerManager(
        docker_image="quant-bench-env:v3.0",
        use_docker=True,
    )
    info = manager.create_container(
        "legacyperm",
        data_dir=str(tmp_path),
        docs_dir=str(tmp_path),
    )

    assert info.container_id == "legacycontai"
    assert _mode(workspace) == 0o755
    manager.destroy_container(info.container_id)

    chown_calls = [
        cmd
        for cmd in calls
        if isinstance(cmd, list)
        and cmd[:8]
        == [
            "docker",
            "exec",
            "--user",
            "root",
            "legacycontai",
            "chown",
            "-R",
            "sandbox:sandbox",
        ]
    ]
    restore_calls = [
        cmd
        for cmd in calls
        if isinstance(cmd, list)
        and cmd[:6] == ["docker", "exec", "--user", "root", "legacycontai", "sh"]
    ]
    assert len(chown_calls) == 1
    assert len(restore_calls) == 1
