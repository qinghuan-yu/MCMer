"""Shared bounded text rules for honest modeling failure contracts."""

from __future__ import annotations

import hashlib
from typing import Any


def bounded_failure_summary(
    message: str,
    diagnostic_name: str,
    *,
    max_length: int = 600,
) -> str:
    suffix = f" Full diagnostic: {diagnostic_name}."
    normalized = " ".join(message.split()) or "Model IR generation failed."
    available = max_length - len(suffix)
    return f"{normalized[:available].rstrip()}{suffix}"


def bounded_subproblem_objective(subproblem: dict[str, Any]) -> str:
    for field in ("objective", "goal", "description", "brief_description", "title"):
        value = str(subproblem.get(field) or "").strip()
        if value:
            return value if len(value) <= 500 else value[:497].rstrip() + "..."
    subproblem_id = str(subproblem.get("id") or "unknown").strip()
    return f"Produce a reviewable model for subproblem {subproblem_id}."


def safe_artifact_component(value: str) -> str:
    component = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    ).strip("._-")
    if component and component == value and len(component) <= 80:
        return component
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{(component or 'subproblem')[:64]}-{digest}"
