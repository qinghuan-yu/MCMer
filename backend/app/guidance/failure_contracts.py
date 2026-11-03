"""Shared bounded text rules for honest modeling failure contracts."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any

from app.guidance.contracts import SubproblemSpec


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


def bounded_subproblem_objective(
    subproblem: SubproblemSpec | Mapping[str, Any],
) -> str:
    payload = (
        subproblem.model_dump(mode="python")
        if isinstance(subproblem, SubproblemSpec)
        else subproblem
    )
    for field in ("objective", "description", "brief_description", "title"):
        value = str(payload.get(field) or "").strip()
        if value:
            return value if len(value) <= 500 else value[:497].rstrip() + "..."
    goals = payload.get("goals")
    if isinstance(goals, list):
        for goal in goals:
            value = str(goal or "").strip()
            if value:
                return value if len(value) <= 500 else value[:497].rstrip() + "..."
    subproblem_id = str(payload.get("id") or "unknown").strip()
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
