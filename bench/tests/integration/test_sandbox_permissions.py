from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from server.core.container import ContainerManager
from server.core.staging import cleanup_staged_dirs, create_staged_dirs


def _docker_image_available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode != 0:
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=10,
        ).returncode
        == 0
    )


def test_sandbox_tools_read_data_and_write_workspace_with_non_1000_host_uid(
    tmp_path: Path,
):
    if os.getuid() == 1000:
        pytest.skip("host UID 1000 masks the prod UID mismatch")

    image = os.environ.get("QTB_TEST_SANDBOX_IMAGE", "quant-bench-env:v3.0")
    if not _docker_image_available(image):
        pytest.skip(f"Docker image unavailable: {image}")

    data_src = tmp_path / "data_src"
    docs_src = tmp_path / "docs_src"
    data_src.mkdir()
    docs_src.mkdir()
    (data_src / "prices.csv").write_text("Date,Close\n2024-01-01,101\n", encoding="utf-8")
    (docs_src / "guide.md").write_text("# Guide\n", encoding="utf-8")

    staged_data, staged_docs, temp_dirs = create_staged_dirs(
        ["prices.csv"],
        ["guide.md"],
        [str(data_src)],
        str(docs_src),
    )
    manager = ContainerManager(docker_image=image, use_docker=True)
    info = None
    try:
        info = manager.create_container(
            "uidperm",
            data_dir=staged_data,
            docs_dir=staged_docs,
            sandbox_image=image,
        )
        manager.start_executor(info.container_id, timeout=30)

        shell_output = manager.call_tool_in_container(
            info.container_id,
            "shell_exec",
            {
                "command": (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('/workspace/shell_probe.txt').write_text('workspace ok')\n"
                    "print(Path('/data/prices.csv').read_text().strip())\n"
                    "PY"
                ),
                "timeout": 30,
            },
            timeout=60,
        )
        write_output = manager.call_tool_in_container(
            info.container_id,
            "file_write",
            {"path": "probe.txt", "content": "ok"},
            timeout=30,
        )
        read_output = manager.call_tool_in_container(
            info.container_id,
            "file_read",
            {"path": "prices.csv"},
            timeout=30,
        )

        assert "2024-01-01,101" in shell_output
        assert "Written 2 bytes" in write_output
        assert "2024-01-01,101" in read_output
    finally:
        if info is not None:
            manager.destroy_container(info.container_id)
        cleanup_staged_dirs(temp_dirs)
