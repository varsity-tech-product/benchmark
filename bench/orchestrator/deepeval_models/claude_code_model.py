"""Claude Code CLI-backed DeepEval model wrapper.

This adapter makes Claude Code available as a plain LLM callable for
DeepEval-based simulator/judge flows. It is intentionally separate from the
benchmark's agent adapter so existing agent behavior remains unchanged.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from deepeval.models.base_model import DeepEvalBaseLLM

_DEFAULT_MAX_BUDGET_USD = 2.0
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_MODEL = "claude-code/sonnet"
_SCOPES = [
    "user:inference",
    "user:profile",
    "user:sessions:claude_code",
]


def _coerce_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _normalize_model_name(model: Optional[str]) -> tuple[str, str]:
    """Return (resolver_name, Claude CLI model name)."""
    if not model or model == "claude-code":
        return _DEFAULT_MODEL, "sonnet"

    if model.startswith("claude-code/"):
        cli_model = model.split("/", 1)[1] or "sonnet"
        return f"claude-code/{cli_model}", cli_model

    return f"claude-code/{model}", model


class ClaudeCodeModel(DeepEvalBaseLLM):
    """Use `claude --print` as a DeepEval model backend."""

    def __init__(
        self,
        model: Optional[str] = None,
        system_prompt: str = "",
        max_budget_usd: Optional[float] = None,
        timeout_seconds: Optional[int] = None,
        cli_path: Optional[str] = None,
    ):
        resolved_name, cli_model = _normalize_model_name(model)
        self.cli_model = cli_model
        self.system_prompt = system_prompt
        self.max_budget_usd = _coerce_float(
            os.environ.get("CLAUDE_CODE_MODEL_MAX_BUDGET_USD"),
            max_budget_usd or _DEFAULT_MAX_BUDGET_USD,
        )
        self.timeout_seconds = _coerce_int(
            os.environ.get("CLAUDE_CODE_MODEL_TIMEOUT_SECONDS"),
            timeout_seconds or _DEFAULT_TIMEOUT_SECONDS,
        )
        self.cli_path = cli_path or os.environ.get("CLAUDE_CODE_CLI_PATH", "claude")
        self._tmp_root: Optional[str] = None
        self._config_dir: Optional[str] = None
        super().__init__(model=resolved_name)

    def load_model(self, *args, **kwargs):
        return self

    def get_model_name(self, *args, **kwargs) -> str:
        return self.name

    def supports_json_mode(self) -> bool:
        return True

    def supports_structured_outputs(self) -> bool:
        return True

    def generate(self, prompt: str, schema=None):
        result, _ = self._run_sync(prompt, schema=schema)
        return result

    async def a_generate(self, prompt: str, schema=None):
        result, cost = await self._run_async(prompt, schema=schema)
        if schema is not None:
            return result
        return result, cost

    def _build_command(self, schema=None) -> list[str]:
        cmd = [
            self.cli_path,
            "--print",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--model",
            self.cli_model,
            "--tools",
            "",
            "--max-budget-usd",
            f"{self.max_budget_usd:.2f}",
        ]
        if self.system_prompt:
            cmd.extend(["--system-prompt", self.system_prompt])
        if schema is not None:
            cmd.extend(["--json-schema", json.dumps(schema.model_json_schema())])
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        access_token = env.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        refresh_token = env.get("CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "")
        if access_token or refresh_token:
            env["CLAUDE_CONFIG_DIR"] = self._ensure_oauth_creds(
                access_token=access_token,
                refresh_token=refresh_token,
            )
        return env

    def _ensure_oauth_creds(self, access_token: str, refresh_token: str) -> str:
        if self._tmp_root is None:
            self._tmp_root = tempfile.mkdtemp(prefix="qtb_claude_code_model_")
            atexit.register(shutil.rmtree, self._tmp_root, ignore_errors=True)

        config_dir = Path(self._tmp_root) / "claude_config"
        config_dir.mkdir(parents=True, exist_ok=True)

        creds = {
            "claudeAiOauth": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresAt": 9999999999999,
                "scopes": list(_SCOPES),
                "subscriptionType": "max",
            }
        }
        creds_path = config_dir / ".credentials.json"
        creds_path.write_text(json.dumps(creds), encoding="utf-8")
        os.chmod(creds_path, 0o600)

        self._config_dir = str(config_dir)
        return self._config_dir

    def _parse_output(self, stdout: str) -> tuple[Any, float]:
        payload = stdout.strip()
        if not payload:
            raise RuntimeError("Claude Code returned no output")

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Claude Code returned invalid JSON: {payload[:400]}"
            ) from exc

        if data.get("is_error"):
            raise RuntimeError(
                data.get("result") or "Claude Code reported an unknown error"
            )

        cost = data.get("total_cost_usd")
        if cost is None:
            cost = sum(
                float(model_usage.get("costUSD", 0.0) or 0.0)
                for model_usage in data.get("modelUsage", {}).values()
            )

        result = data.get("result", "")
        if data.get("structured_output") is not None:
            result = data["structured_output"]

        return result, float(cost or 0.0)

    def _coerce_schema_result(self, result: Any, schema):
        if schema is None:
            return result

        if isinstance(result, schema):
            return result

        if isinstance(result, str):
            result = json.loads(result)

        return schema.model_validate(result)

    def _run_sync(self, prompt: str, schema=None) -> tuple[Any, float]:
        try:
            proc = subprocess.run(
                self._build_command(schema=schema),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._build_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Claude Code CLI not found. Install it from "
                "https://docs.anthropic.com/en/docs/claude-code"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Claude Code timed out after {self.timeout_seconds}s"
            ) from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"Claude Code exited with code {proc.returncode}: {stderr}"
            )

        result, cost = self._parse_output(proc.stdout)
        return self._coerce_schema_result(result, schema), cost

    async def _run_async(self, prompt: str, schema=None) -> tuple[Any, float]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._build_command(schema=schema),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Claude Code CLI not found. Install it from "
                "https://docs.anthropic.com/en/docs/claude-code"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Claude Code timed out after {self.timeout_seconds}s"
            ) from exc

        if proc.returncode != 0:
            message = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Claude Code exited with code {proc.returncode}: {message}"
            )

        result, cost = self._parse_output(stdout.decode("utf-8", errors="replace"))
        return self._coerce_schema_result(result, schema), cost
