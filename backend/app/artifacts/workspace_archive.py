"""Build auditable task-workspace archives with an explicit evidence contract."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceArchiveContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkspaceFileRecord(WorkspaceArchiveContract):
    path: str
    size_bytes: int
    sha256: str


class OperatorVersionRecord(WorkspaceArchiveContract):
    node_id: str
    operator_id: str
    version: str


class WorkspaceArtifactGroups(WorkspaceArchiveContract):
    original_inputs: list[str] = Field(default_factory=list)
    input_catalog: list[str] = Field(default_factory=list)
    evidence_profile: list[str] = Field(default_factory=list)
    fact_traceability: list[str] = Field(default_factory=list)
    model_ir: list[str] = Field(default_factory=list)
    model_reviews: list[str] = Field(default_factory=list)
    execution_plan: list[str] = Field(default_factory=list)
    operator_versions: list[OperatorVersionRecord] = Field(default_factory=list)
    code_exports: list[str] = Field(default_factory=list)
    registries: list[str] = Field(default_factory=list)
    verification_reports: list[str] = Field(default_factory=list)
    figures: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)


class WorkspaceArchiveManifest(WorkspaceArchiveContract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    execution_status: str
    completeness_status: Literal["complete", "partial"]
    missing_required_groups: list[str] = Field(default_factory=list)
    artifact_groups: WorkspaceArtifactGroups
    files: list[WorkspaceFileRecord]


class WorkspaceArchiveIncompleteError(ValueError):
    def __init__(self, missing_required_groups: list[str]) -> None:
        self.missing_required_groups = tuple(missing_required_groups)
        super().__init__(
            "passed execution is missing required workspace evidence: "
            + ", ".join(missing_required_groups)
        )


def build_workspace_archive(task_id: str, work_dir: Path, *, task_status: str | None = None) -> Path:
    files = _workspace_files(work_dir)
    records = [_file_record(work_dir, path) for path in files]
    digests = {record.path: record.sha256 for record in records}
    execution = _read_json(work_dir / "execution_manifest.json") or {}
    status = execution.get("status")
    execution_status = status if isinstance(status, str) and status else "unknown"
    groups, operator_versions_complete = _artifact_groups(work_dir, list(digests))
    missing = _missing_required_groups(groups, operator_versions_complete)
    if task_status == "completed" and execution_status != "passed":
        missing.append("execution_manifest")
    if not _figures_complete(work_dir, groups, digests):
        missing.append("figures")
    for field, group in (("source_files_sha256", "original_inputs"), ("input_files_sha256", "original_inputs"),
                         ("output_files_sha256", "execution_outputs")):
        snapshots = execution.get(field, {})
        if not _snapshots_match(snapshots, digests):
            missing.append(group)
    declared = execution.get("generated_files", [])
    if not isinstance(declared, list) or any(not isinstance(path, str) or path not in digests for path in declared):
        missing.append("execution_outputs")
    missing = list(dict.fromkeys(missing))
    if missing and (execution_status == "passed" or task_status == "completed"):
        raise WorkspaceArchiveIncompleteError(missing)

    manifest = WorkspaceArchiveManifest(
        task_id=task_id,
        execution_status=execution_status,
        completeness_status="complete" if not missing else "partial",
        missing_required_groups=missing,
        artifact_groups=groups,
        files=records,
    )
    archive_path = _temporary_archive_path(task_id)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(work_dir).as_posix())
        archive.writestr(
            "workspace_manifest.json",
            manifest.model_dump_json(indent=2),
        )
    return archive_path


def _workspace_files(work_dir: Path) -> list[Path]:
    root = work_dir.resolve()
    files: list[Path] = []
    for path in sorted(work_dir.rglob("*")):
        if path.is_symlink() or not path.is_file() or _is_generated_archive(work_dir, path):
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        files.append(path)
    return files


def _is_generated_archive(work_dir: Path, path: Path) -> bool:
    if path.parent != work_dir or path.suffix.lower() != ".zip":
        return False
    name = path.name.lower()
    return name == "workspace.zip" or name.endswith("-workspace.zip")


def _artifact_groups(
    work_dir: Path,
    relative_paths: list[str],
) -> tuple[WorkspaceArtifactGroups, bool]:
    available = set(relative_paths)
    original_inputs = sorted(
        path
        for path in relative_paths
        if path.split("/", 1)[0] in {"data", "inputs", "source_bundle"}
    )
    input_catalog = ["input_catalog.json"] if "input_catalog.json" in available else []
    evidence_profile = ["evidence_profile.json"] if "evidence_profile.json" in available else []
    fact_traceability = sorted(available & {
        "approved_model_ir.json",
        "problem_spec.json",
    })
    model_ir = sorted(available & {
        "model_ir.json",
        "model_ir_initial.json",
        "approved_model_ir.json",
    })
    model_reviews = sorted(available & {
        "model_review.json",
        "model_review_submission.json",
        "revision_request.json",
    })
    execution_plan = ["execution_plan.json"] if "execution_plan.json" in available else []
    operator_versions, versions_complete = _operator_versions(work_dir / "execution_plan.json")
    code_exports = sorted(
        path
        for path in relative_paths
        if path == "notebook.ipynb" or path.startswith("code/")
    )
    registries = sorted(available & {
        "parameter_registry.json",
        "result_registry.json",
        "figure_registry.json",
    })
    verification_reports = sorted(available & {
        "verification_report.json",
        "guidance_audit_report.json",
    })
    figures = sorted(
        path
        for path in relative_paths
        if path.startswith("figures/")
        and Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".pdf"}
    )
    guidance = ["guidance.md"] if "guidance.md" in available else []
    return WorkspaceArtifactGroups(
        original_inputs=original_inputs,
        input_catalog=input_catalog,
        evidence_profile=evidence_profile,
        fact_traceability=fact_traceability,
        model_ir=model_ir,
        model_reviews=model_reviews,
        execution_plan=execution_plan,
        operator_versions=operator_versions,
        code_exports=code_exports,
        registries=registries,
        verification_reports=verification_reports,
        figures=figures,
        guidance=guidance,
    ), versions_complete


def _operator_versions(path: Path) -> tuple[list[OperatorVersionRecord], bool]:
    payload = _read_json(path)
    if payload is None:
        return [], False
    graph = payload.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return [], False
    records: list[OperatorVersionRecord] = []
    for node in nodes:
        if not isinstance(node, dict):
            return records, False
        node_id = node.get("node_id")
        operator_id = node.get("operator_id")
        version = node.get("operator_version")
        if not all(isinstance(value, str) and value.strip() for value in (node_id, operator_id, version)):
            return records, False
        records.append(OperatorVersionRecord(
            node_id=node_id,
            operator_id=operator_id,
            version=version,
        ))
    return records, True


def _missing_required_groups(
    groups: WorkspaceArtifactGroups,
    operator_versions_complete: bool,
) -> list[str]:
    missing: list[str] = []
    if not groups.original_inputs:
        missing.append("original_inputs")
    if groups.input_catalog != ["input_catalog.json"]:
        missing.append("input_catalog")
    if groups.evidence_profile != ["evidence_profile.json"]:
        missing.append("evidence_profile")
    if groups.fact_traceability != ["approved_model_ir.json", "problem_spec.json"]:
        missing.append("fact_traceability")
    if not {"model_ir.json", "approved_model_ir.json"}.issubset(groups.model_ir):
        missing.append("model_ir")
    if not {"model_review.json", "model_review_submission.json"}.issubset(
        groups.model_reviews
    ):
        missing.append("model_reviews")
    if groups.execution_plan != ["execution_plan.json"]:
        missing.append("execution_plan")
    if not operator_versions_complete:
        missing.append("operator_versions")
    if not groups.code_exports:
        missing.append("code_exports")
    if set(groups.registries) != {
        "parameter_registry.json",
        "result_registry.json",
        "figure_registry.json",
    }:
        missing.append("registries")
    if set(groups.verification_reports) != {
        "verification_report.json",
        "guidance_audit_report.json",
    }:
        missing.append("verification_reports")
    if groups.guidance != ["guidance.md"]:
        missing.append("guidance")
    return missing


def _figures_complete(root: Path, groups: WorkspaceArtifactGroups, files: dict[str, str]) -> bool:
    registry = _read_json(root / "figure_registry.json") or {}
    figures = registry.get("figures")
    if not isinstance(figures, list):
        return False
    summary = registry.get("summary", {})
    if not isinstance(summary, dict) or summary.get("total_count", len(figures)) != len(figures):
        return False
    registered = set()
    for entry in figures:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path or path not in files:
            return False
        registered.add(path)
    groups.figures = sorted(set(groups.figures) | registered)
    report = _read_json(root / "verification_report.json") or {}
    refs = report.get("artifact_refs", {})
    if not isinstance(refs, dict):
        return False
    if "figures" in refs:
        if not isinstance(refs["figures"], list):
            return False
        for entry in refs["figures"]:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if not isinstance(path, str) or path not in registered:
                return False
    contract = _read_json(root / "problem_contract.json") or {}
    if contract.get("paper_requires_figures") is True and not registered:
        return False
    plan = _read_json(root / "execution_plan.json") or {}
    graph = plan.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if isinstance(node, dict) and node.get("operator_id") == "plot.xy_evidence":
            parameters = node.get("parameters")
            planned_path = parameters.get("path") if isinstance(parameters, dict) else None
            if not isinstance(planned_path, str) or planned_path not in registered:
                return False
    return True


def _snapshots_match(snapshots: object, digests: dict[str, str]) -> bool:
    if not isinstance(snapshots, dict):
        return False
    for reference, expected in snapshots.items():
        # Source citations may address a paragraph in a registered file.
        path = PurePosixPath(reference.partition("#")[0].replace("\\", "/")).as_posix()
        if not isinstance(expected, str) or digests.get(path) != expected:
            return False
    return True


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _file_record(work_dir: Path, path: Path) -> WorkspaceFileRecord:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return WorkspaceFileRecord(
        path=path.relative_to(work_dir).as_posix(),
        size_bytes=path.stat().st_size,
        sha256=digest.hexdigest(),
    )


def _temporary_archive_path(task_id: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix=f"{task_id}-workspace-",
        suffix=".zip",
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path
