"""Scope output references to their producer and select fields from runtime JSON values."""
from __future__ import annotations

from collections.abc import Collection


def split_output_reference(reference: str, outputs: Collection[str]) -> tuple[str, str]:
    """An exact alias wins, including existing aliases that contain dots."""
    output = reference
    while output and output not in outputs:
        output = output.rpartition(".")[0]
    if not output:
        raise ValueError(f"unknown output in value_ref {reference!r}")
    path = reference[len(output) + 1:]
    if reference != output and (not path or not all(path.split("."))):
        raise ValueError(f"empty field in value_ref {reference!r}")
    return output, path


def artifact_field(artifact: object, path: str) -> object:
    value = artifact
    for part in path.split("."):
        if part and isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError(f"artifact path does not exist: {path}")
    return value


def supporting_output_ref(subproblem_id: str, output: str) -> str:
    def segment(value):
        return value.replace("~", "~0").replace("/", "~1")

    return f"result_registry.json#/supporting_outputs/{segment(subproblem_id)}/{segment(output)}"
