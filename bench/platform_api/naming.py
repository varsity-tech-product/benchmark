"""Canonical platform/reference naming decisions for RFC-A v0."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalName:
    existing_name: str
    canonical_name: str
    owner: str
    note: str = ""


CANONICAL_NAMES: tuple[CanonicalName, ...] = (
    CanonicalName(
        existing_name="StudentSimulator",
        canonical_name="NPCProvider",
        owner="platform_api.contracts",
        note="Reference implementations use RefPersonaNPC.",
    ),
    CanonicalName(
        existing_name="TutoringSession",
        canonical_name="Session",
        owner="platform runtime",
    ),
    CanonicalName(
        existing_name="StudentPersona",
        canonical_name="Persona",
        owner="reference implementation",
    ),
    CanonicalName(
        existing_name="student_opening",
        canonical_name="payload.student_opening",
        owner="reference task payload",
    ),
    CanonicalName(
        existing_name="TUTOR_SYSTEM_PROMPT",
        canonical_name="reference internal constant",
        owner="reference implementation",
    ),
)
