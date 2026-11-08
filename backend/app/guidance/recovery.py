"""输入快照驱动的合同/算子恢复，以及流水线公开工件失效。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable

from pydantic import BaseModel


def canonical_digest(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def workspace_file(root: Path, reference: str) -> Path:
    relative = PurePosixPath(reference.split("#", 1)[0].replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
        raise ValueError(f"recovery artifact escapes workspace: {reference}")
    path = (root / str(relative)).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"recovery artifact escapes workspace: {reference}")
    return path


def file_snapshots(root: Path, references: Iterable[str]) -> dict[str, str | None]:
    result = {}
    for reference in sorted(set(references)):
        path = workspace_file(root, reference)
        result[reference] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


def model_source_snapshots(root: Path, model) -> dict[str, str | None]:
    return file_snapshots(root, (
        item.source_ref for subproblem in model.subproblems
        if subproblem.execution_status == "solvable" for item in subproblem.data
    ))


def generated_figure_paths(value) -> list[str]:
    if isinstance(value, list):
        return [path for item in value for path in generated_figure_paths(item)]
    if isinstance(value, dict):
        own = [value["path"]] if {"path", "role", "linked_result_ids"} <= value.keys() else []
        return own + [path for item in value.values() for path in generated_figure_paths(item)]
    return []


class RecoveryCheckpoint:
    """每个拥有者只保留一个已验证结果；失败不会回退到旧输入的结果。"""

    def __init__(self, root: Path | None, namespace: str, owner: str):
        self.root = root
        self.path = root / ".recovery" / namespace / f"{canonical_digest(owner)}.json" if root else None

    def load(self, context):
        if self.path is None:
            return None
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if record["input_sha256"] != canonical_digest(context):
                return None
            if record["value_sha256"] != canonical_digest(record["value"]):
                return None
            files = record["files"]
            if any(value is None for value in files.values()) or file_snapshots(self.root, files) != files:
                return None
            return record["value"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def discard(self) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)

    def save(self, context, value, *, files: Iterable[str] = ()) -> None:
        if self.path is None:
            return
        record = {
            "input_sha256": canonical_digest(context), "value_sha256": canonical_digest(value),
            "value": value, "files": file_snapshots(self.root, files),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        temporary.replace(self.path)


STAGE_OUTPUTS = {
    "decomposition": ("problem_spec.json", "model_ir_initial.json", "revision_request.json"),
    "modeling": ("model_ir.json",),
    "model_review": ("model_review.json", "approved_model_ir.json"),
    "calculation": ("compilation_result.json", "execution_plan.json", "execution_manifest.json",
                    "parameter_registry.json", "result_registry.json", "figure_registry.json", "notebook.ipynb"),
    "result_verification": ("verification_report.json",),
    "guidance": ("guidance.md", "res.md", "guidance_context.json", "guidance_audit_report.json"),
}


def invalidate_stage_outputs(root: Path, stage: str) -> None:
    stages = list(STAGE_OUTPUTS)
    for dependent in stages[stages.index(stage):]:
        for name in STAGE_OUTPUTS[dependent]:
            workspace_file(root, name).unlink(missing_ok=True)
