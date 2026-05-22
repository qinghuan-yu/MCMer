"""Figure gate stage facade.

This keeps ArtifactRegistry/FigureBundle plumbing out of ``workflow.py`` while
the legacy figure stage is migrated into deterministic skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.artifacts.registry import ArtifactRegistry, FigureBundle
from app.core.figure_stage import FigureStage as LegacyFigureStage


@dataclass(frozen=True)
class FigureBundleSnapshot:
    """Artifact-gated paper figure bundle prepared for writer stages."""

    registry: ArtifactRegistry
    artifact_valid_images: list[str]
    figure_bundle: FigureBundle
    figure_bundle_payload: dict[str, Any]
    writer_images: list[str]


class FigureGateStage:
    """Facade for ArtifactRegistry validation and FigureBundle creation."""

    @staticmethod
    def from_registry(registry: ArtifactRegistry) -> FigureBundleSnapshot:
        artifact_valid_images = registry.validate_for_paper()
        bundle_snapshot = LegacyFigureStage.build_bundle_snapshot(registry)
        figure_bundle = bundle_snapshot.get("figure_bundle")
        writer_images = bundle_snapshot.get("writer_images", [])
        if figure_bundle is None or not isinstance(writer_images, list):
            raise ValueError("Failed to resolve writer image bundle snapshot.")
        figure_bundle_payload = bundle_snapshot.get("figure_bundle_dict", figure_bundle.to_dict())
        if not isinstance(figure_bundle_payload, dict):
            figure_bundle_payload = figure_bundle.to_dict()
        return FigureBundleSnapshot(
            registry=registry,
            artifact_valid_images=list(artifact_valid_images),
            figure_bundle=figure_bundle,
            figure_bundle_payload=figure_bundle_payload,
            writer_images=list(writer_images),
        )
