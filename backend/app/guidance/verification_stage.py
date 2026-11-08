"""Guidance 职责边界：verification_stage。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.guidance.contracts import ModelAdequacyLayer, ProblemSpec, VerificationLayer, VerificationReport
from app.guidance.model_ir_compiler import ExecutionPlan
from app.guidance.model_ir_semantics import validate_model_fact_coverage
from app.guidance.model_review_contracts import ApprovedModelIR
from app.guidance.model_review_validation import assess_mathematical_review
from app.guidance.model_validators import build_default_model_validator_registry
from app.guidance.operator_execution import OperatorExecutionManifest
from app.guidance.stage_io import (
    _read_json,
    _required_input,
    _write_model,
)


class ResultVerificationStage:
    """Verify the operator execution graph without inferring model adequacy."""

    async def run(
        self,
        *,
        task_id: str,
        task: dict,
        input_path: Path | None,
        output_path: Path,
    ) -> Path:
        manifest = OperatorExecutionManifest.model_validate_json(
            _required_input(input_path).read_text(encoding="utf-8")
        )
        root = output_path.parent
        approved = ApprovedModelIR.model_validate_json(
            (root / "approved_model_ir.json").read_text(encoding="utf-8")
        )
        execution_plan = ExecutionPlan.model_validate_json(
            (root / manifest.execution_plan_ref).read_text(encoding="utf-8")
        )
        parameters = _read_json(root / "parameter_registry.json")
        results = _read_json(root / "result_registry.json")
        figure_registry = _read_json(root / "figure_registry.json")

        execution_issues: list[str] = []
        if manifest.status not in {"passed", "partial"}:
            execution_issues.append(f"operator execution status is {manifest.status}")
        missing = [
            path for path in manifest.generated_files if not (root / path).is_file()
        ]
        if missing:
            execution_issues.append(f"missing generated files: {', '.join(missing)}")
        plan_hash = hashlib.sha256(
            execution_plan.model_dump_json().encode("utf-8")
        ).hexdigest()
        if plan_hash != manifest.execution_plan_sha256:
            execution_issues.append("execution plan hash does not match manifest")
        expected_node_ids = [node.node_id for node in execution_plan.graph.nodes]
        if manifest.status == "partial":
            verified_subproblem_ids = {
                str(item.get("subproblem_id") or "")
                for item in (results.get("verified_results") or [])
                if isinstance(item, dict) and item.get("subproblem_id")
            }
            ordered_executed = [
                node.node_id
                for node in execution_plan.graph.nodes
                if node.node_id in set(manifest.executed_node_ids)
            ]
            required_verified_nodes = [
                node.node_id
                for node in execution_plan.graph.nodes
                if node.subproblem_id in verified_subproblem_ids
            ]
            if (
                not required_verified_nodes
                or manifest.executed_node_ids != ordered_executed
                or not set(required_verified_nodes).issubset(manifest.executed_node_ids)
            ):
                execution_issues.append("executed node IDs do not match execution plan order")
        elif manifest.executed_node_ids != expected_node_ids:
            execution_issues.append("executed node IDs do not match execution plan order")

        evidence_issues, figures = _operator_evidence_issues(
            root=root,
            executed_node_ids=set(manifest.executed_node_ids),
            parameter_registry=parameters,
            result_registry=results,
            figure_registry=figure_registry,
        )
        coverage_issues = _blocked_result_issues(results)
        problem_path = root / "problem_spec.json"
        problem_spec = (
            ProblemSpec.model_validate_json(problem_path.read_text(encoding="utf-8"))
            if problem_path.is_file() else None
        )
        model_adequacy = _assess_model_adequacy(
            approved, results, problem_spec=problem_spec,
            mathematical_review=assess_mathematical_review(approved, root=root, problem_spec=problem_spec),
        )
        if execution_issues or evidence_issues:
            model_adequacy["numerical_validation"]["status"] = "blocked"
            model_adequacy["numerical_validation"]["reasons"].append(
                "numerical validation is blocked until execution and evidence layers pass"
            )
            model_adequacy = ModelAdequacyLayer.model_validate(model_adequacy).model_dump()
        counterevidence_records = _build_counterevidence_records(
            approved,
            model_adequacy,
        )

        report = VerificationReport(
            model_id=manifest.model_id,
            coverage_status=(
                "partial"
                if approved.review_status == "partial" or results.get("blocked_results")
                else "complete"
            ),
            execution_verified={
                "status": "passed" if not execution_issues else "failed",
                "reasons": execution_issues or ["execution plan hash and node coverage verified"],
                "evidence_refs": [manifest.execution_plan_ref, input_path.name],
            },
            evidence_supported={
                "status": (
                    "blocked" if execution_issues else "passed" if not evidence_issues else "failed"
                ),
                "reasons": evidence_issues or ["registered outputs trace to executed operator nodes"],
                "evidence_refs": [
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
                ],
            },
            model_adequate=model_adequacy,
            counterevidence_records=counterevidence_records,
            input_checks=[{
                "status": "passed" if not missing else "failed",
                "checked_files": manifest.generated_files,
                "missing_files": missing,
            }],
            blocking_issues=[*execution_issues, *evidence_issues, *coverage_issues],
            artifact_refs={
                "approved_model": "approved_model_ir.json",
                "model_review": approved.review_ref,
                **(
                    {"revision_request": approved.revision_request_ref}
                    if approved.revision_request_ref is not None
                    else {}
                ),
                "execution_manifest": input_path.name,
                "execution_plan": manifest.execution_plan_ref,
                "parameter_registry": "parameter_registry.json",
                "result_registry": "result_registry.json",
                "figure_registry": "figure_registry.json",
                "figures": figures,
            },
        )
        _write_model(output_path, report)
        return output_path

def _blocked_result_issues(result_registry: dict) -> list[str]:
    blocked_results = result_registry.get("blocked_results")
    if not isinstance(blocked_results, list):
        return []

    issues: list[str] = []
    for item in blocked_results:
        if not isinstance(item, dict):
            continue
        subproblem_id = str(item.get("subproblem_id") or item.get("id") or "unknown")
        reason = str(item.get("reason") or item.get("message") or "result unavailable")
        recovery_action = str(item.get("recovery_action") or "").strip()
        issue = f"subproblem {subproblem_id} blocked: {reason}"
        if recovery_action:
            issue += f"; recovery: {recovery_action}"
        issues.append(issue)
    return issues

def _assess_model_adequacy(
    approved: ApprovedModelIR,
    result_registry: dict,
    *,
    problem_spec: ProblemSpec | None,
    mathematical_review: VerificationLayer,
) -> dict:
    return ModelAdequacyLayer(
        source_coverage=_assess_source_coverage(approved, problem_spec),
        mathematical_review=mathematical_review,
        numerical_validation=_assess_numerical_validation(approved, result_registry),
    ).model_dump()


def _assess_source_coverage(
    approved: ApprovedModelIR,
    problem_spec: ProblemSpec | None,
) -> dict:
    if problem_spec is None or not problem_spec.locked_facts:
        return {
            "status": "not_assessed",
            "reasons": ["no ProblemSpec with locked source facts was registered"],
        }
    try:
        expected_ids = {item.id for item in problem_spec.subproblems}
        modeled_ids = {item.subproblem_id for item in approved.approved_model_ir.subproblems}
        if expected_ids != modeled_ids:
            raise ValueError("ProblemSpec and approved model subproblem IDs do not match")
        validate_model_fact_coverage(problem_spec, approved.approved_model_ir)
    except ValueError as exc:
        status, reasons = "failed", [str(exc)]
    else:
        status, reasons = "passed", [
            "required fact IDs and model paths are covered for executable subproblems; "
            "this checks references, not mathematical equivalence"
        ]
    return {
        "status": status,
        "reasons": reasons,
        "evidence_refs": ["problem_spec.json", "approved_model_ir.json"],
    }


def _assess_numerical_validation(
    approved: ApprovedModelIR,
    result_registry: dict,
) -> dict:
    registry = build_default_model_validator_registry()
    verified_results = result_registry.get("verified_results")
    if not isinstance(verified_results, list):
        verified_results = []

    candidate_roles: set[str] = set()
    checks: dict[str, str] = {}
    reasons: list[str] = []
    evidence_refs: list[str] = []
    family_statuses: list[str] = []

    blocked_subproblem_ids = {
        str(item.get("subproblem_id") or "")
        for item in (result_registry.get("blocked_results") or [])
        if isinstance(item, dict) and item.get("subproblem_id")
    }

    for subproblem in approved.approved_model_ir.subproblems:
        if subproblem.execution_status != "solvable":
            continue
        subproblem_results = [
            result
            for result in verified_results
            if isinstance(result, dict)
            and result.get("subproblem_id") == subproblem.subproblem_id
        ]
        if not subproblem_results and subproblem.subproblem_id in blocked_subproblem_ids:
            continue
        roles = {candidate.role for candidate in subproblem.candidate_models}
        candidate_roles.update(roles)
        evidence = [
            result["value"]
            for result in subproblem_results
            if isinstance(result.get("value"), dict)
        ]
        for result in subproblem_results:
            result_id = str(result.get("id") or "")
            reference = f"result_registry.json#{result_id}"
            if result_id and reference not in evidence_refs:
                evidence_refs.append(reference)

        for family in subproblem.validation.model_families:
            assessment = registry.assess(
                family=family,
                candidate_roles=roles,
                evidence=evidence,
            )
            family_statuses.append(assessment.status)
            prefix = f"{subproblem.subproblem_id}.{family}"
            for name, check in assessment.checks.items():
                checks[f"{prefix}.{name}"] = check.status
            if assessment.reasons:
                reasons.extend(
                    f"{prefix}: {reason}" for reason in assessment.reasons
                )

    if family_statuses and all(status == "passed" for status in family_statuses):
        status = "passed"
        reasons.append("all declared numerical validators passed for the supplied model")
    elif "failed" in family_statuses:
        status = "failed"
    else:
        status = "not_assessed"
        if not reasons:
            reasons.append("no assessable model-family evidence was registered")

    return {
        "status": status,
        "candidate_roles": sorted(candidate_roles) or ["alternative"],
        "checks": checks,
        "reasons": reasons,
        "evidence_refs": evidence_refs or ["approved_model_ir.json"],
    }

def _build_counterevidence_records(
    approved: ApprovedModelIR,
    model_adequacy: dict,
) -> list[dict]:
    checks = model_adequacy["numerical_validation"]["checks"]
    if not isinstance(checks, dict):
        checks = {}
    report_evidence_refs = model_adequacy.get("evidence_refs")
    if not isinstance(report_evidence_refs, list):
        report_evidence_refs = []

    records: list[dict] = []
    for subproblem in approved.approved_model_ir.subproblems:
        if subproblem.execution_status != "solvable":
            continue
        prefix = f"{subproblem.subproblem_id}."
        subproblem_checks = {
            name: status
            for name, status in checks.items()
            if name.startswith(prefix)
        }
        passed_refs = [
            name for name, status in subproblem_checks.items() if status == "passed"
        ]
        failed_refs = [
            name for name, status in subproblem_checks.items() if status == "failed"
        ]
        baseline_comparison_passed = any(
            name.endswith(".baseline_comparison")
            for name in passed_refs
        )

        for candidate in subproblem.candidate_models:
            if candidate.role == "recommended":
                continue
            if (
                candidate.role == "baseline"
                and model_adequacy.get("status") == "passed"
                and baseline_comparison_passed
            ):
                status = "excluded"
                reason = (
                    "the recommended route passed the registered baseline comparison "
                    "and every declared model-family validator"
                )
                check_refs = passed_refs
                evidence_refs = list(report_evidence_refs)
            elif failed_refs:
                status = "not_excluded"
                reason = (
                    "the available validation evidence does not justify excluding "
                    "this route"
                )
                check_refs = failed_refs
                evidence_refs = list(report_evidence_refs)
            else:
                status = "not_assessed"
                reason = "candidate-specific exclusion evidence was not produced"
                check_refs = []
                evidence_refs = []
            records.append({
                "subproblem_id": subproblem.subproblem_id,
                "challenged_candidate_id": candidate.id,
                "status": status,
                "exclusion_reason": reason,
                "check_refs": check_refs,
                "evidence_refs": evidence_refs,
                "applicability_boundary": candidate.applicability_boundary,
            })
    return records

def _operator_evidence_issues(
    *,
    root: Path,
    executed_node_ids: set[str],
    parameter_registry: dict,
    result_registry: dict,
    figure_registry: dict,
) -> tuple[list[str], list[dict]]:
    issues: list[str] = []
    parameters = parameter_registry.get("parameters")
    if not isinstance(parameters, list):
        issues.append("parameter_registry.parameters must be a list")
        parameters = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            issues.append("parameter registry contains a non-object entry")
            continue
        source_node = str(parameter.get("source_node") or "")
        if source_node not in executed_node_ids:
            issues.append(
                f"parameter {parameter.get('parameter_id')} has an unexecuted source node"
            )

    verified_results = result_registry.get("verified_results")
    blocked_results = result_registry.get("blocked_results")
    if not isinstance(verified_results, list):
        issues.append("result_registry.verified_results must be a list")
        verified_results = []
    if not isinstance(blocked_results, list):
        issues.append("result_registry.blocked_results must be a list")
    result_ids: set[str] = set()
    for result in verified_results:
        if not isinstance(result, dict):
            issues.append("result registry contains a non-object verified entry")
            continue
        result_id = str(result.get("id") or "")
        result_ids.add(result_id)
        source_node = str(result.get("source_node") or "")
        if source_node not in executed_node_ids:
            issues.append(f"result {result_id} has an unexecuted source node")
        evidence_ids = result.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            issues.append(f"result {result_id} has no evidence node IDs")
        else:
            unknown = sorted(set(map(str, evidence_ids)) - executed_node_ids)
            if unknown:
                issues.append(
                    f"result {result_id} references unexecuted evidence nodes: {', '.join(unknown)}"
                )

    figures = figure_registry.get("figures")
    if not isinstance(figures, list):
        issues.append("figure_registry.figures must be a list")
        figures = []
    for figure in figures:
        if not isinstance(figure, dict):
            issues.append("figure registry contains a non-object entry")
            continue
        path = str(figure.get("path") or "")
        if not path or not (root / path).is_file():
            issues.append(f"registered figure was not generated: {path or '<missing path>'}")
        linked_result_ids = figure.get("linked_result_ids")
        if not isinstance(linked_result_ids, list) or not linked_result_ids:
            issues.append(f"figure {path or '<missing path>'} has no linked result IDs")
            continue
        unknown_ids = set(map(str, linked_result_ids)) - result_ids
        if unknown_ids:
            issues.append(
                f"figure has invalid result IDs for {path}: {', '.join(sorted(unknown_ids))}"
            )

    return issues, [figure for figure in figures if isinstance(figure, dict)]
