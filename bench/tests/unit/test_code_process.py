from types import SimpleNamespace

from eval.programmatic.code_process import (
    _is_code_exec,
    evaluate_code_lifecycle,
)


def _shell(command: str, *, success: bool = True, result: str = "ok"):
    return SimpleNamespace(
        name="shell_exec",
        args={"command": command},
        success=success,
        result=result,
    )


def test_python_heredoc_without_dash_counts_as_code_execution():
    logs = [
        _shell(
            "python3 << 'EOF'\nraise RuntimeError('boom')\nEOF",
            success=False,
            result="Traceback (most recent call last): boom",
        ),
        _shell("python3 << 'EOF'\nprint('fixed')\nEOF", result="fixed"),
    ]

    assert all(_is_code_exec(log) for log in logs)
    result = evaluate_code_lifecycle(logs)

    assert result["applicable"] is True
    assert result["score"] == 1.0
    assert result["sub_scores"] == {
        "iterative_refinement": None,
        "test_before_deliver": 1.0,
        "error_recovery": 1.0,
        "code_evolution": None,
    }


def test_python_heredoc_under_workspace_prefix_counts_as_code_execution():
    log = _shell("cd /workspace && python3 << 'EOF'\nprint(1)\nEOF")

    assert _is_code_exec(log) is True


def test_dash_stdin_and_script_python_commands_still_count():
    assert _is_code_exec(_shell("python3 - << 'EOF'\nprint(1)\nEOF")) is True
    assert _is_code_exec(_shell("python3 script.py")) is True
    assert _is_code_exec(_shell("python3 -c 'print(1)'")) is True
