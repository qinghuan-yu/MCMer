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
    references = [
        item.source_ref for subproblem in model.subproblems
        if subproblem.execution_status == "solvable" for item in subproblem.data
    ]
    references.extend(name for name in ("input_catalog.json", "evidence_profile.json") if (root / name).is_file())
    return file_snapshots(root, references)


def generated_figure_paths(value) -> list[str]:
    if isinstance(value, list):
        return [path for item in value for path in generated_figure_paths(item)]
    if isinstance(value, dict):
        own = [value["path"]] if {"path", "role", "linked_result_ids"} <= value.keys() else []
        return own + [path for item in value.values() for path in generated_figure_paths(item)]
    return []


def load_source_adjudication(root: Path, problem_spec):
    """重载独立语义许可，始终对照提议产生时的题意和当前原件。"""
    from app.guidance.contracts import ProblemSpec
    from app.guidance.source_adjudication import (
        SourceAdjudicationRecord,
        validate_source_adjudication,
    )

    path = root / "source_adjudication.json"
    if not path.is_file():
        return None
    previous_path = root / "problem_spec_initial.json"
    prior_problem = (
        ProblemSpec.model_validate_json(previous_path.read_text(encoding="utf-8"))
        if previous_path.is_file() else problem_spec
    )
    profile_path = root / "evidence_profile.json"
    if not profile_path.is_file():
        raise ValueError("source adjudication is missing its evidence snapshot")
    catalog_path = root / "input_catalog.json"
    record = SourceAdjudicationRecord.model_validate_json(path.read_text(encoding="utf-8"))
    validate_source_adjudication(
        record, problem_spec=prior_problem,
        input_catalog=json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else None,
        evidence_profile=json.loads(profile_path.read_text(encoding="utf-8")),
        work_dir=root,
    )
    return record


def load_source_resolution(root: Path, problem_spec):
    """读取已提交的来源裁决，并从保留快照和原件重新核验。"""
    from app.guidance.contracts import ProblemSpec
    from app.guidance.source_resolution import SourceResolutionRecord, validate_source_resolution

    path = root / "source_resolution.json"
    if not path.is_file():
        return None
    previous_path = root / "problem_spec_initial.json"
    profile_path = root / "evidence_profile.json"
    if not previous_path.is_file() or not profile_path.is_file():
        raise ValueError("source resolution is missing its preserved problem or evidence snapshot")
    adjudication = load_source_adjudication(root, problem_spec)
    if adjudication is None:
        raise ValueError("source resolution requires independent source adjudication permission")
    record = SourceResolutionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    catalog_path = root / "input_catalog.json"
    validate_source_resolution(
        record,
        previous_problem_spec=ProblemSpec.model_validate_json(previous_path.read_text(encoding="utf-8")),
        problem_spec=problem_spec,
        input_catalog=json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else None,
        evidence_profile=json.loads(profile_path.read_text(encoding="utf-8")),
        work_dir=root,
        adjudication=adjudication,
    )
    return record


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
    "decomposition": ("problem_spec.json", "problem_spec_initial.json", "source_resolution.json",
                      "source_adjudication.json",
                      "model_ir_initial.json", "approved_model_ir_initial.json", "revision_request.json"),
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
