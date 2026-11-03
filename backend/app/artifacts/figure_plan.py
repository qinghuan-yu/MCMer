"""figure plan responsibilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from app.artifacts.visualization_protocol import normalize_verified_results
from app.utils.file_io import save_json
from app.artifacts.figure_feasibility import _apply_capability_constraints, _discover_input_files, _issues_from_requests, _load_result_registry, _load_verified_result_ids
from app.artifacts.figure_need import FigureNeedDetector
from app.artifacts.figure_request_planning import _backfill_requests, _inject_fallback_result_overview_requests, _planning_basis_from_reasons, _seed_from_existing_requests, _solve_spec_from_requests


class FigurePlanBuilder:
    """Build and persist figure_plan.json as the source of truth."""



    @classmethod
    def build(
        cls,
        question: str,
        solve_spec: dict[str, Any],
        problem_facts: dict[str, Any] | None,
        chart_language: str,
        work_dir: str,
    ) -> dict[str, Any]:
        need = FigureNeedDetector.detect(question, solve_spec, problem_facts, work_dir)
        requires_result_requested = bool(
            need.requires_result_figures
            or (isinstance(solve_spec, dict) and solve_spec.get("requires_result_figures") is True)
            or (isinstance(solve_spec, dict) and solve_spec.get("requires_result_figures_by_problem") is True)
        )
        requires_any_requested = bool(
            need.requires_figures
            or requires_result_requested
            or (isinstance(solve_spec, dict) and solve_spec.get("requires_figures") is True)
            or (isinstance(solve_spec, dict) and solve_spec.get("requires_figures_by_problem") is True)
        )
        requests = _seed_from_existing_requests(solve_spec, chart_language)
        available_data_files = _discover_input_files(work_dir)
        verified_result_ids = _load_verified_result_ids(work_dir)
        verified_results = normalize_verified_results(_load_result_registry(work_dir))

        # Rule-based backfill if detector says figures are needed but requests are empty/incomplete.
        if requires_any_requested:
            requests = _backfill_requests(
                requests=requests,
                solve_spec=solve_spec,
                chart_language=chart_language,
                domain_type=need.domain_type,
                detected_needs=need.detected_needs,
                available_data_files=available_data_files,
            )

        requests = _apply_capability_constraints(
            requests=requests,
            chart_language=chart_language,
            available_data_files=available_data_files,
            verified_result_ids=verified_result_ids,
            work_dir=work_dir,
        )

        if requires_any_requested and not any(
            isinstance(item, dict) and bool(item.get("required", True))
            for item in requests
        ):
            requests = _inject_fallback_result_overview_requests(
                requests=requests,
                solve_spec=solve_spec,
                chart_language=chart_language,
                available_data_files=available_data_files,
                verified_results=verified_results,
            )
            requests = _apply_capability_constraints(
                requests=requests,
                chart_language=chart_language,
                available_data_files=available_data_files,
                verified_result_ids=verified_result_ids,
                work_dir=work_dir,
            )

        if requires_result_requested and not any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
            for item in requests
        ):
            requests = _inject_fallback_result_overview_requests(
                requests=requests,
                solve_spec=solve_spec,
                chart_language=chart_language,
                available_data_files=available_data_files,
                verified_results=verified_results,
            )
            requests = _apply_capability_constraints(
                requests=requests,
                chart_language=chart_language,
                available_data_files=available_data_files,
                verified_result_ids=verified_result_ids,
                work_dir=work_dir,
            )

        required_count = sum(1 for item in requests if isinstance(item, dict) and bool(item.get("required", True)))
        required_result_count = sum(
            1
            for item in requests
            if isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
        )
        required_explanatory_count = sum(
            1
            for item in requests
            if isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "").strip().lower() == "explanatory_figure"
        )
        requires_result_figures = any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
            for item in requests
        )
        requires_explanatory_figures = any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "").strip().lower() == "explanatory_figure"
            for item in requests
        )

        plan = {
            "requires_figures": bool(requires_any_requested),
            "requires_result_figures": bool(requires_result_requested),
            "requires_explanatory_figures": bool(need.requires_explanatory_figures),
            "requires_figures_by_problem": bool(requires_any_requested),
            "requires_result_figures_by_problem": bool(requires_result_requested),
            "requires_explanatory_figures_by_problem": bool(need.requires_explanatory_figures),
            "confidence": need.confidence,
            "reasons": need.reasons,
            "detected_needs": need.detected_needs,
            "domain_type": need.domain_type,
            "planning_basis": _planning_basis_from_reasons(need.reasons),
            "required_feasible_count": required_count,
            "required_feasible_result_count": required_result_count,
            "required_feasible_explanatory_count": required_explanatory_count,
            "required_count": required_count,
            "recommended_count": sum(1 for item in requests if isinstance(item, dict) and str(item.get("status") or "") == "recommended"),
            "blocked_count": sum(1 for item in requests if isinstance(item, dict) and str(item.get("status") or "") == "blocked"),
            "workflow_issues": [
                issue.to_dict()
                for issue in _issues_from_requests(requests)
            ],
            "figure_requests": requests,
        }

        return plan






    @classmethod
    def save(cls, work_dir: str, figure_plan: dict[str, Any]) -> str:
        path = str(Path(work_dir) / "figure_plan.json")
        save_json(figure_plan, path)
        return path

    @classmethod
    def reevaluate_requests(
        cls,
        requests: list[dict[str, Any]],
        chart_language: str,
        work_dir: str,
    ) -> list[dict[str, Any]]:
        available_data_files = _discover_input_files(work_dir)
        verified_result_ids = _load_verified_result_ids(work_dir)
        verified_results = normalize_verified_results(_load_result_registry(work_dir))
        normalized = [dict(item) for item in requests if isinstance(item, dict)]
        had_required_request = any(
            isinstance(item, dict) and bool(item.get("required", True))
            for item in normalized
        )
        had_result_request = any(
            isinstance(item, dict)
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
            for item in normalized
        )
        evaluated = _apply_capability_constraints(
            requests=normalized,
            chart_language=chart_language,
            available_data_files=available_data_files,
            verified_result_ids=verified_result_ids,
            work_dir=work_dir,
        )
        has_required_request = any(
            isinstance(item, dict) and bool(item.get("required", True))
            for item in evaluated
        )
        has_required_result_request = any(
            isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
            for item in evaluated
        )
        if had_required_request and (not has_required_request or (had_result_request and not has_required_result_request)):
            pseudo_solve_spec = _solve_spec_from_requests(normalized)
            before_count = len(evaluated)
            evaluated = _inject_fallback_result_overview_requests(
                requests=evaluated,
                solve_spec=pseudo_solve_spec,
                chart_language=chart_language,
                available_data_files=available_data_files,
                verified_results=verified_results,
            )
            if len(evaluated) > before_count:
                evaluated = _apply_capability_constraints(
                    requests=evaluated,
                    chart_language=chart_language,
                    available_data_files=available_data_files,
                    verified_result_ids=verified_result_ids,
                    work_dir=work_dir,
                )
        return evaluated
