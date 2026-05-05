"""Reference business helpers for the server runtime."""

from server.reference.bundle_loader import (
    REFERENCE_BUNDLE_CONFIG,
    load_reference_bundle,
)
from server.reference.prompts import (
    EMOTIONAL_PROFILE_DESCRIPTIONS,
    RefSystemPrompt,
    build_reference_user_description,
)

__all__ = [
    "EMOTIONAL_PROFILE_DESCRIPTIONS",
    "REFERENCE_BUNDLE_CONFIG",
    "RefSystemPrompt",
    "build_reference_user_description",
    "load_reference_bundle",
]
