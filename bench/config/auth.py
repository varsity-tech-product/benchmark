"""OAuth token resolution for QuantTutorBench.

Reads the latest OAuth access token from the macOS Keychain
(populated by Claude Code), falling back to the CLAUDE_CODE_OAUTH_TOKEN
environment variable when the Keychain is unavailable.
"""

import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def get_oauth_token() -> str:
    """Return the freshest OAuth access token available.

    Resolution order:
      1. macOS Keychain  (``Claude Code-credentials``)
      2. Environment variable ``CLAUDE_CODE_OAUTH_TOKEN``

    Returns an empty string when no token is found.
    """
    # --- 1. macOS Keychain ---
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout.strip())
            oauth_data = data.get("claudeAiOauth", {})
            if isinstance(oauth_data, str):
                oauth_data = json.loads(oauth_data)
            token = oauth_data.get("accessToken", "")
            if token:
                log.debug("OAuth token resolved from macOS Keychain")
                return token
    except FileNotFoundError:
        pass  # Not macOS — ``security`` binary missing
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as exc:
        log.debug("Keychain read failed: %s", exc)

    # --- 2. Environment variable fallback ---
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if token:
        log.debug("OAuth token resolved from CLAUDE_CODE_OAUTH_TOKEN env var")
    return token
