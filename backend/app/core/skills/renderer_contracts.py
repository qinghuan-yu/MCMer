"""renderer contracts responsibilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RendererResult:
    created_images: list[str] = field(default_factory=list)
    satisfied: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    renderer: str = "RendererSkill"

    @classmethod
    def from_legacy(cls, payload: dict[str, Any]) -> "RendererResult":
        return cls(
            created_images=[
                str(item).strip()
                for item in (payload.get("created_images", []) if isinstance(payload, dict) else [])
                if str(item).strip()
            ],
            satisfied=[
                item for item in (payload.get("satisfied", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
            missing=[
                item for item in (payload.get("missing", []) if isinstance(payload, dict) else [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
