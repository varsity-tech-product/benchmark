"""Re-exports from config/prompt_config.py for backward compatibility.

All prompt definitions live in config/prompt_config.py.
This module re-exports them so that adapter relative imports
(e.g. ``from .prompts import TUTOR_SYSTEM_PROMPT``) continue to work.
"""

from config.prompt_config import (  # noqa: F401
    BASELINE_SYSTEM_PROMPT,
    TUTOR_SYSTEM_PROMPT,
    build_scenario,
    build_tutor_context,
    build_user_description,
)
