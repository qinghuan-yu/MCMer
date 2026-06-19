"""Business stages for the high-trust guidance pipeline."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol

from app.guidance.contracts import (
    ApprovedModelSpec,
    GeneratedProgram,
    ModelReview,
    ModelSpec,
    ProblemSpec,
    ExecutionManifest,
    VerificationReport,
)
from app.guidance.execution import LocalProgramExecutor
from app.artifacts.guidance_audit import build_guidance_audit_report
from app.guidance.renderer import render_verified_guidance


class ProblemDecomposer(Protocol):
    async def decompose(
        self,
        *,
        question: str,
        data_files: list[str],
        task: dict,
    ) -> ProblemSpec: ...


class Modeler(Protocol):
    async def model(
        self,
        *,
        problem_spec: ProblemSpec,
        task: dict,
        work_dir: Path,
    ) -> ModelSpec: ...


class ModelReviewer(Protocol):
    async def review(
        self,
        *,
        model_spec: ModelSpec,
        task: dict,
        work_dir: Path,
    ) -> ModelReview: ...


class ProgramGenerator(Protocol):
    async def generate(
        self,
        *,
        approved_model: ApprovedModelSpec,
        task: dict,
        work_dir: Path,
    ) -> GeneratedProgram: ...


class DecompositionStage:
    def __init__(self, *, decomposer: ProblemDecomposer) -> None:
        self.decomposer = decomposer

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        root = output_path.parent
        problem_spec = await self.decomposer.decompose(
            question=str(task.get("source_question") or task.get("question") or "").strip(),
            data_files=_list_data_files(root),
            task=task,
        )
        _write_model(output_path, problem_spec)
        return output_path


class ModelingStage:
    def __init__(self, *, modeler: Modeler) -> None:
        self.modeler = modeler

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        problem_spec = ProblemSpec.model_validate_json(_required_input(input_path).read_text(encoding="utf-8"))
        model_spec = await self.modeler.model(
            problem_spec=problem_spec,
            task=task,
            work_dir=output_path.parent,
        )
        _write_model(output_path, model_spec)
        return output_path


class ModelReviewStage:
    def __init__(self, *, reviewer: ModelReviewer) -> None:
        self.reviewer = reviewer

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        model_spec = ModelSpec.model_validate_json(_required_input(input_path).read_text(encoding="utf-8"))
        review = await self.reviewer.review(
            model_spec=model_spec,
            task=task,
            work_dir=output_path.parent,
        )
        _write_model(output_path.parent / "model_review.json", review)
        approved = ApprovedModelSpec(
            review_status=review.status,
            model_id=model_spec.model_id,
            approved_model=model_spec.model_dump(mode="json"),
            required_outputs=model_spec.required_outputs,
        )
        _write_model(output_path, approved)
        return output_path


class CalculationStage:
    """Generate one complete solver and execute it exactly once."""

    def __init__(
        self,
        *,
        program_generator: ProgramGenerator,
        executor: LocalProgramExecutor,
    ) -> None:
        self.program_generator = program_generator
        self.executor = executor

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        if input_path is None:
            raise ValueError("CalculationStage requires approved_model_spec.json")

        approved_model = ApprovedModelSpec.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        work_dir = output_path.parent
        program = await self.program_generator.generate(
            approved_model=approved_model,
            task=task,
            work_dir=work_dir,
        )
        await self.executor.execute(
            task_id=task_id,
            work_dir=work_dir,
            approved_model=approved_model,
            program=program,
        )
        return output_path


class ResultVerificationStage:
    """Verify execution evidence and registered residual metrics."""

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        manifest = ExecutionManifest.model_validate_json(
            _required_input(input_path).read_text(encoding="utf-8")
        )
        root = output_path.parent
        missing = [path for path in manifest.generated_files if not (root / path).is_file()]
        results = _read_json(root / "result_registry.json")
        verified_results = results.get("verified_results", [])
        registered_metrics = _residual_metrics(verified_results)
        numerical_checks = _verify_solver_results(
            _read_json(root / "solver_results.json"),
            registered_metrics,
        )
        review = _read_json(root / "model_review.json")
        units_passed = str(review.get("dimensional_consistency") or "").lower() in {
            "passed",
            "consistent",
            "dimensionless",
            "not_applicable",
        }

        blocking_issues: list[str] = []
        if manifest.status != "passed":
            blocking_issues.append(f"solver status is {manifest.status}")
        if missing:
            blocking_issues.append(f"missing generated files: {', '.join(missing)}")
        if not verified_results:
            blocking_issues.append("result_registry has no verified results")
        if not registered_metrics:
            blocking_issues.append("verified results have no finite residual metrics")
        blocking_issues.extend(numerical_checks["issues"])
        if not units_passed:
            blocking_issues.append("model review did not pass dimensional consistency")

        report = VerificationReport(
            model_id=manifest.model_id,
            status="verified" if not blocking_issues else "blocked",
            input_checks=[{
                "status": "passed" if not missing else "failed",
                "checked_files": manifest.generated_files,
                "missing_files": missing,
            }],
            equation_checks=[{
                "status": "passed" if numerical_checks["equations_match"] else "failed",
                "max_residual_difference": numerical_checks["max_residual_difference"],
            }],
            unit_checks=[{
                "status": "passed" if units_passed else "failed",
                "review_value": review.get("dimensional_consistency"),
            }],
            constraint_checks=[{
                "status": "passed" if numerical_checks["constraints_hold"] else "failed",
                "checked": "branch flows are nonnegative",
            }],
            residual_checks=[{
                "status": "passed" if numerical_checks["metrics_match"] else "failed",
                "registered_metrics": registered_metrics,
                "recomputed_metrics": numerical_checks["recomputed_metrics"],
            }],
            sensitivity_checks=[{"status": "not_run", "reason": "Phase 1 vertical slice"}],
            blocking_issues=blocking_issues,
            artifact_refs={
                "approved_model": "approved_model_spec.json",
                "execution_manifest": input_path.name,
                "parameter_registry": "parameter_registry.json",
                "result_registry": "result_registry.json",
                "figures": [
                    path for path in manifest.generated_files
                    if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp"}
                ],
            },
        )
        _write_model(output_path, report)
        return output_path


class GuidanceStage:
    """Render guidance only from a verification report and its artifact references."""

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        report = VerificationReport.model_validate_json(
            _required_input(input_path).read_text(encoding="utf-8")
        )
        root = output_path.parent
        refs = report.artifact_refs
        approved = _read_json(root / str(refs["approved_model"]))
        parameters = _read_json(root / str(refs["parameter_registry"]))
        results = _read_json(root / str(refs["result_registry"]))
        guidance = render_verified_guidance(
            approved_model=approved,
            verification_report=report.model_dump(mode="json"),
            parameter_registry=parameters,
            result_registry=results,
        )
        context = {
            "artifact_type": "guidance",
            "required_sections": [
                "可信度摘要",
                "问题理解",
                "数据与来源",
                "参数与来源表",
                "模型选择理由",
                "分问题建模步骤",
                "必要计算结果",
                "复核与稳健性",
                "阻断项与待确认项",
                "论文转化建议",
                "可复现附件说明",
            ],
        }
        audit = build_guidance_audit_report(
            guidance_markdown=guidance,
            guidance_context=context,
            result_registry=results,
            parameter_registry=parameters,
        )
        output_path.write_text(guidance, encoding="utf-8")
        (root / "res.md").write_text(guidance, encoding="utf-8")
        (root / "guidance_context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "guidance_audit_report.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return output_path


def _required_input(input_path: Path | None) -> Path:
    if input_path is None:
        raise ValueError("stage requires the previous saved contract")
    return input_path


def _list_data_files(root: Path) -> list[str]:
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in data_dir.rglob("*") if path.is_file())


def _write_model(path: Path, model) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _residual_metrics(verified_results) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if not isinstance(verified_results, list):
        return metrics
    for result in verified_results:
        raw_metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
        if not isinstance(raw_metrics, dict):
            continue
        for name, value in raw_metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                metrics[str(name)] = float(value)
    return metrics


def _verify_solver_results(
    solver_results: dict,
    registered_metrics: dict[str, float],
) -> dict[str, object]:
    observed = _numeric_list(solver_results.get("observed"))
    recomposed = _numeric_list(solver_results.get("recomposed"))
    residual = _numeric_list(solver_results.get("residual"))
    issues: list[str] = []
    same_length = bool(observed) and len(observed) == len(recomposed) == len(residual)
    if not same_length:
        issues.append("solver_results arrays are missing or have inconsistent lengths")
        return {
            "equations_match": False,
            "constraints_hold": False,
            "metrics_match": False,
            "max_residual_difference": None,
            "recomputed_metrics": {},
            "issues": issues,
        }

    expected_residual = [actual - predicted for actual, predicted in zip(observed, recomposed)]
    max_residual_difference = max(
        abs(expected - recorded) for expected, recorded in zip(expected_residual, residual)
    )
    equations_match = max_residual_difference <= 1e-8
    if not equations_match:
        issues.append("recorded residuals do not equal observed minus recomposed values")

    recomputed_metrics = {
        "rmse": math.sqrt(sum(value * value for value in expected_residual) / len(expected_residual)),
        "max_abs_residual": max(abs(value) for value in expected_residual),
    }
    metrics_match = all(
        name in registered_metrics
        and math.isclose(registered_metrics[name], value, rel_tol=1e-8, abs_tol=1e-8)
        for name, value in recomputed_metrics.items()
    )
    if not metrics_match:
        issues.append("registered residual metrics do not match independent recomputation")

    branch_values = [
        *_numeric_list(solver_results.get("branch1")),
        *_numeric_list(solver_results.get("branch2")),
    ]
    constraints_hold = bool(branch_values) and min(branch_values) >= -1e-8
    if not constraints_hold:
        issues.append("branch-flow nonnegativity constraint failed or was not recorded")

    return {
        "equations_match": equations_match,
        "constraints_hold": constraints_hold,
        "metrics_match": metrics_match,
        "max_residual_difference": max_residual_difference,
        "recomputed_metrics": recomputed_metrics,
        "issues": issues,
    }


def _numeric_list(value) -> list[float]:
    if not isinstance(value, list):
        return []
    output: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item):
            return []
        output.append(float(item))
    return output
