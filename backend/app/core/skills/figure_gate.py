"""Figure gate skill facade.

This gives the workflow a deterministic skill boundary while preserving the
existing ArtifactRegistry implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.artifacts.contracts import ProblemContract
from app.artifacts.registry import ArtifactRegistry, FigureBundle


@dataclass(frozen=True)
class FigureGateResult:
    accepted_images: list[str]
    rejected: list[dict[str, Any]]
    bundle: FigureBundle


class FigureGateSkill:
    """Validate and bundle paper figures for writer consumption."""

    @staticmethod
    def validate(work_dir: str, contract: ProblemContract | None = None) -> FigureGateResult:
        registry = ArtifactRegistry.load(work_dir, contract=contract)
        accepted = registry.validate_for_paper()
        bundle = registry.build_figure_bundle()
        return FigureGateResult(
            accepted_images=accepted,
            rejected=registry.rejection_summary(),
            bundle=bundle,
        )
