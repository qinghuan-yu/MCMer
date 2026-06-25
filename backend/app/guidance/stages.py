"""Business stages for the high-trust guidance pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.guidance.artifacts import (
    FigureRegistry,
    ParameterRegistry,
    ResultRegistry,
    SolverResults,
)
from app.guidance.contracts import (
    ApprovedModelSpec,
    GeneratedProgram,
    ModelReview,
    ModelSpec,
    RevisedModelSpec,
    ProblemSpec,
    ExecutionManifest,
    VerificationReport,
    execution_required_outputs,
    validate_review_resolutions,
    validate_subproblem_coverage,
)
from app.guidance.execution import LocalProgramExecutor
from app.guidance.verification import verify_solver_results
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
    ) -> RevisedModelSpec: ...

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model: ModelSpec,
        review: ModelReview,
        task: dict,
        work_dir: Path,
    ) -> ModelSpec: ...


class ModelReviewer(Protocol):
    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
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
        source_path = _required_input(input_path)
        if source_path.name == "problem_spec.json":
            problem_spec = ProblemSpec.model_validate_json(source_path.read_text(encoding="utf-8"))
            model_spec = await self.modeler.model(
                problem_spec=problem_spec,
                task=task,
                work_dir=output_path.parent,
            )
        else:
            review = ModelReview.model_validate_json(source_path.read_text(encoding="utf-8"))
            problem_spec = ProblemSpec.model_validate_json(
                (output_path.parent / review.problem_spec_ref).read_text(encoding="utf-8")
            )
            previous_model = ModelSpec.model_validate_json(
                (output_path.parent / review.model_spec_ref).read_text(encoding="utf-8")
            )
            model_spec = await self.modeler.revise(
                problem_spec=problem_spec,
                previous_model=previous_model,
                review=review,
                task=task,
                work_dir=output_path.parent,
            )
            validate_review_resolutions(model_spec, review)
        validate_subproblem_coverage(problem_spec, model_spec)
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
        problem_spec = ProblemSpec.model_validate_json(
            (output_path.parent / model_spec.problem_spec_ref).read_text(encoding="utf-8")
        )
        review = await self.reviewer.review(
            problem_spec=problem_spec,
            model_spec=model_spec,
            task=task,
            work_dir=output_path.parent,
        )
        review = review.model_copy(
            update={
                "problem_spec_ref": model_spec.problem_spec_ref,
                "model_spec_ref": input_path.name,
            }
        )
        _write_model(output_path.parent / "model_review.json", review)
        approved = ApprovedModelSpec(
            review_status=review.status,
            model_id=model_spec.model_id,
            approved_model=model_spec.model_dump(mode="json"),
            required_outputs=execution_required_outputs(model_spec),
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
        try:
            program = await self.program_generator.generate(
                approved_model=approved_model,
                task=task,
                work_dir=work_dir,
            )
        except Exception as exc:
            (work_dir / "calculation_failure.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "model_id": approved_model.model_id,
                        "failed_operation": "program_generation",
                        "resume_from": input_path.name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise
        await self.executor.execute(
            task_id=task_id,
            work_dir=work_dir,
            approved_model=approved_model,
            program=program,
        )
        return output_path


class ResultVerificationStage:
    """Verify only solver artifacts that satisfy the typed file contracts."""

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
        missing = list(dict.fromkeys([
            *manifest.missing_required_outputs,
            *[path for path in manifest.generated_files if not (root / path).is_file()],
        ]))
        contract_issues: list[str] = []
        parameters = _load_artifact_contract(
            root / "parameter_registry.json", ParameterRegistry, contract_issues
        )
        results = _load_artifact_contract(
            root / "result_registry.json", ResultRegistry, contract_issues
        )
        figure_registry = _load_artifact_contract(
            root / "figure_registry.json", FigureRegistry, contract_issues
        )
        solver_results = _load_artifact_contract(
            root / "solver_results.json", SolverResults, contract_issues
        )
        result_ids = {item.id for item in results.verified_results} if results else set()
        figures, figure_issues = _verify_typed_figures(
            manifest, figure_registry, result_ids
        )
        numerical_checks = (
            verify_solver_results(solver_results, results)
            if solver_results is not None and results is not None
            else {
                "equation_checks": [],
                "constraint_checks": [],
                "residual_checks": [],
                "sensitivity_checks": [],
                "issues": [],
            }
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
        blocking_issues.extend(contract_issues)
        if parameters is not None and not parameters.parameters:
            blocking_issues.append("parameter_registry has no parameter evidence")
        if results is not None and not results.verified_results:
            blocking_issues.append("result_registry has no verified results")
        blocking_issues.extend(numerical_checks["issues"])
        blocking_issues.extend(figure_issues)
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
            equation_checks=numerical_checks["equation_checks"],
            unit_checks=[{
                "status": "passed" if units_passed else "failed",
                "review_value": review.get("dimensional_consistency"),
            }],
            constraint_checks=numerical_checks["constraint_checks"],
            residual_checks=numerical_checks["residual_checks"],
            sensitivity_checks=numerical_checks["sensitivity_checks"],
            blocking_issues=blocking_issues,
            artifact_refs={
                "approved_model": "approved_model_spec.json",
                "execution_manifest": input_path.name,
                "parameter_registry": "parameter_registry.json",
                "result_registry": "result_registry.json",
                "figure_registry": "figure_registry.json",
                "figures": figures,
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
        source_path = _required_input(input_path)
        root = output_path.parent
        if source_path.name == "approved_model_spec.json":
            report, approved, parameters, results = _build_blocked_guidance_artifacts(
                root,
                source_path,
            )
        else:
            report = VerificationReport.model_validate_json(
                source_path.read_text(encoding="utf-8")
            )
            refs = report.artifact_refs
            approved = _read_json(root / str(refs["approved_model"]))
            parameters = _read_json_if_exists(root / str(refs["parameter_registry"]))
            results = _read_json_if_exists(root / str(refs["result_registry"]))
            report = _enforce_verified_guidance_evidence(
                report,
                approved_model=approved,
                parameter_registry=parameters,
                result_registry=results,
            )
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
            figure_registry={
                "figures": report.artifact_refs.get("figures", []),
            },
            artifact_root=root,
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


def _read_json_if_exists(path: Path) -> dict:
    return _read_json(path) if path.is_file() else {}


def _enforce_verified_guidance_evidence(
    report: VerificationReport,
    *,
    approved_model: dict,
    parameter_registry: dict,
    result_registry: dict,
) -> VerificationReport:
    if report.status != "verified":
        return report

    issues: list[str] = []
    parameters = parameter_registry.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        issues.append("verified guidance evidence missing: parameter evidence")
    verified_results = result_registry.get("verified_results")
    if not isinstance(verified_results, list) or not verified_results:
        issues.append("verified guidance evidence missing: numerical results")

    check_groups = {
        "input checks": report.input_checks,
        "equation checks": report.equation_checks,
        "unit checks": report.unit_checks,
        "constraint checks": report.constraint_checks,
        "residual checks": report.residual_checks,
        "sensitivity checks": report.sensitivity_checks,
    }
    for label, checks in check_groups.items():
        if not checks:
            issues.append(f"verified guidance evidence missing: {label}")
        elif any(str(check.get("status")) != "passed" for check in checks):
            issues.append(f"verified guidance evidence failed: {label}")

    model = approved_model.get("approved_model")
    model = model if isinstance(model, dict) else {}
    subproblems = model.get("subproblem_models")
    if not isinstance(subproblems, list) or not subproblems:
        issues.append("verified guidance evidence missing: subproblem modeling details")
    else:
        for subproblem in subproblems:
            if not isinstance(subproblem, dict) or not all(
                isinstance(subproblem.get(field), list) and subproblem[field]
                for field in ("equations", "parameters", "solve_steps")
            ):
                issues.append("verified guidance evidence incomplete: subproblem modeling details")
                break

    if not issues:
        return report
    return report.model_copy(
        update={
            "status": "blocked",
            "blocking_issues": [*report.blocking_issues, *issues],
        }
    )


_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}


def _load_artifact_contract(
    path: Path,
    contract_type: type[BaseModel],
    issues: list[str],
) -> BaseModel | None:
    try:
        return contract_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        issues.append(f"{path.name} contract invalid: {exc}")
        return None


def _verify_typed_figures(
    manifest: ExecutionManifest,
    registry: FigureRegistry | None,
    result_ids: set[str],
) -> tuple[list[dict], list[str]]:
    generated_figures = {
        path for path in manifest.generated_files if Path(path).suffix.lower() in _FIGURE_SUFFIXES
    }
    figures = registry.figures if registry is not None else []
    registered_paths = {figure.path for figure in figures}
    issues: list[str] = []

    for path in sorted(generated_figures - registered_paths):
        issues.append(f"generated figure is not registered: {path}")
    for path in sorted(registered_paths - generated_figures):
        issues.append(f"registered figure was not generated: {path}")
    for figure in figures:
        unknown_ids = set(figure.linked_result_ids) - result_ids
        if unknown_ids:
            issues.append(
                f"figure has invalid result IDs for {figure.path}: {', '.join(sorted(unknown_ids))}"
            )

    return [figure.model_dump(mode="json") for figure in figures], issues


def _build_blocked_guidance_artifacts(
    root: Path,
    approved_path: Path,
) -> tuple[VerificationReport, dict, dict, dict]:
    approved_model = ApprovedModelSpec.model_validate_json(
        approved_path.read_text(encoding="utf-8")
    )
    review = ModelReview.model_validate_json(
        (root / approved_model.review_ref).read_text(encoding="utf-8")
    )
    issues = [*review.blocking_issues, *review.required_revisions]
    if not issues:
        issues = [f"model review ended with status {review.status}"]

    parameters = {
        "parameters": [],
        "summary": {"total_count": 0, "blocked_count": 0},
    }
    results = {
        "verified_results": [],
        "blocked_results": [
            {
                "id": "result_model_review_blocked",
                "message": issue,
                "reason": "model_review_failed",
                "source_data": [approved_model.model_spec_ref, approved_model.review_ref],
            }
            for issue in issues
        ],
        "summary": {
            "coverage_status": "blocked",
            "verified_count": 0,
            "blocked_count": len(issues),
        },
    }
    report = VerificationReport(
        model_id=approved_model.model_id,
        status="blocked",
        blocking_issues=issues,
        artifact_refs={
            "approved_model": approved_path.name,
            "model_review": approved_model.review_ref,
            "parameter_registry": "parameter_registry.json",
            "result_registry": "result_registry.json",
            "figures": [],
        },
    )
    _write_model(root / "verification_report.json", report)
    (root / "parameter_registry.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "result_registry.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, approved_model.model_dump(mode="json"), parameters, results
