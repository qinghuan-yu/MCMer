"""Result-driven visualization planning skill.

The long-term workflow should ask this deterministic skill for figure requests
instead of asking an agent or scanning directories.  It reads verified results
and emits protocol-shaped figure requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.protocols import RESULT_OVERVIEW_ROLE, canonical_caption_for_role, normalize_figure_role
from app.artifacts.visualization_protocol import (
    normalize_verified_results,
    result_overview_request,
)


class VisualizationPlannerSkill:
    """Build fallback-safe figure requests from verified results."""

    RESULT_TYPE_VIEW_MAP: dict[str, list[str]] = {
        "regression": ["fitting_curve", "residual_plot", RESULT_OVERVIEW_ROLE],
        "regression_model": ["fitting_curve", "residual_plot", RESULT_OVERVIEW_ROLE],
        "classification": ["confusion_matrix", "feature_importance", RESULT_OVERVIEW_ROLE],
        "classification_model": ["confusion_matrix", "feature_importance", RESULT_OVERVIEW_ROLE],
        "optimization": ["convergence_curve", "sensitivity_curve", RESULT_OVERVIEW_ROLE],
        "optimization_result": ["convergence_curve", "sensitivity_curve", RESULT_OVERVIEW_ROLE],
        "grouped_decision_result": ["group_bar", "risk_curve", RESULT_OVERVIEW_ROLE],
        "descriptive_statistics": ["histogram", "boxplot", RESULT_OVERVIEW_ROLE],
        "statistical_result": ["histogram", "boxplot", RESULT_OVERVIEW_ROLE],
        "unknown": [RESULT_OVERVIEW_ROLE],
    }

    SUPPORTED_RENDERED_VIEWS = {
        RESULT_OVERVIEW_ROLE,
        "comparison_bar",
        "fitting_curve",
        "residual_plot",
    }

    @staticmethod
    def result_overview_requests(
        result_registry: dict[str, Any],
        chart_language: str,
        default_source_data: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        seen_sections: set[str] = set()
        source_data = default_source_data or ["result_registry.json"]

        for result in normalize_verified_results(result_registry):
            section = result.section or "problem_1"
            if section in seen_sections:
                continue
            seen_sections.add(section)
            requests.append(
                result_overview_request(
                    result=result,
                    chart_language=chart_language,
                    data_files=result.data_artifacts or source_data,
                )
            )
        return requests

    @classmethod
    def build_figure_plan(
        cls,
        result_registry: dict[str, Any],
        chart_language: str,
        work_dir: str,
        solve_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build figure_plan.json payload from verified result contracts.

        This is the long-term primary path.  The old solve_spec/question
        heuristic planner can still be used before verified results exist.
        """
        default_sources = cls.discover_tabular_sources(work_dir) or ["result_registry.json"]
        requests = cls.figure_requests_from_results(
            result_registry=result_registry,
            chart_language=chart_language,
            default_source_data=default_sources,
        )
        if not requests and bool((solve_spec or {}).get("requires_result_figures", True)):
            requests = cls.result_overview_requests(
                result_registry=result_registry,
                chart_language=chart_language,
                default_source_data=default_sources,
            )

        reevaluated = FigurePlanBuilder.reevaluate_requests(
            requests=requests,
            chart_language=chart_language,
            work_dir=work_dir,
        ) if requests else []
        required_requests = [
            item for item in reevaluated
            if isinstance(item, dict) and bool(item.get("required", True))
        ]
        required_result_requests = [
            item for item in required_requests
            if str(item.get("figure_kind") or "result_figure").strip().lower() == "result_figure"
        ]
        blocked_requests = [
            item for item in reevaluated
            if isinstance(item, dict) and str(item.get("status") or "") == "blocked"
        ]
        recommended_requests = [
            item for item in reevaluated
            if isinstance(item, dict) and str(item.get("status") or "") == "recommended"
        ]
        return {
            "planner": "VisualizationPlannerSkill",
            "source": "result_registry",
            "requires_figures": bool(required_requests),
            "requires_result_figures": bool(required_result_requests),
            "requires_explanatory_figures": False,
            "requires_figures_by_problem": bool(required_requests),
            "requires_result_figures_by_problem": bool(required_result_requests),
            "requires_explanatory_figures_by_problem": False,
            "required_feasible_count": len(required_requests),
            "required_feasible_result_count": len(required_result_requests),
            "required_feasible_explanatory_count": 0,
            "required_count": len(required_requests),
            "recommended_count": len(recommended_requests),
            "blocked_count": len(blocked_requests),
            "workflow_issues": cls._workflow_issues_from_requests(reevaluated),
            "figure_requests": reevaluated,
        }

    @classmethod
    def figure_requests_from_results(
        cls,
        result_registry: dict[str, Any],
        chart_language: str,
        default_source_data: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        source_data = default_source_data or ["result_registry.json"]
        for result in normalize_verified_results(result_registry):
            contract = result.visualization_contract
            if contract and contract.visualization_exemption:
                continue
            required_views = list(contract.required_views) if contract else []
            candidate_views = list(contract.candidate_views) if contract else []
            fallback_views = list(contract.fallback_views) if contract else [RESULT_OVERVIEW_ROLE]
            if not required_views:
                required_views = [
                    view for view in cls.RESULT_TYPE_VIEW_MAP.get(result.result_type, cls.RESULT_TYPE_VIEW_MAP["unknown"])
                    if view in cls.SUPPORTED_RENDERED_VIEWS
                ]
            views = [*required_views, *candidate_views, *fallback_views]
            normalized_views = []
            for view in views:
                role = normalize_figure_role(view)
                if role not in cls.SUPPORTED_RENDERED_VIEWS:
                    continue
                if role not in normalized_views:
                    normalized_views.append(role)
            if RESULT_OVERVIEW_ROLE not in normalized_views:
                normalized_views.append(RESULT_OVERVIEW_ROLE)

            for index, view in enumerate(normalized_views):
                request = cls._request_for_view(
                    result=result,
                    view_type=view,
                    chart_language=chart_language,
                    data_files=result.data_artifacts or source_data,
                    required=index == 0 or view == RESULT_OVERVIEW_ROLE,
                )
                requests.append(request)
        return cls._dedupe_requests(requests)

    @staticmethod
    def _request_for_view(
        result: Any,
        view_type: str,
        chart_language: str,
        data_files: list[str],
        required: bool = True,
    ) -> dict[str, Any]:
        if view_type == RESULT_OVERVIEW_ROLE:
            request = result_overview_request(result=result, chart_language=chart_language, data_files=data_files)
            request["required"] = required
            request["must_include_in_paper"] = required
            return request

        section = result.section or "problem_1"
        caption = canonical_caption_for_role(view_type, chart_language) or view_type
        required_columns = {
            "comparison_bar": ["method", "metric", "value"],
            "fitting_curve": ["x", "observed", "fitted"],
            "residual_plot": ["x", "residual"],
        }.get(view_type, [])
        return {
            "id": f"fig_{section}_{view_type}",
            "problem_section": section,
            "subproblem_id": section,
            "figure_kind": "result_figure",
            "semantic_role": view_type,
            "role": view_type,
            "canonical_caption": caption,
            "purpose": caption,
            "chart_intent": view_type,
            "view_type": view_type,
            "required": required,
            "must_include_in_paper": required,
            "chart_language": chart_language,
            "language": chart_language,
            "required_columns": required_columns,
            "required_data": data_files,
            "depends_on": {
                "model_objects": [],
                "formulas": [],
                "data_files": data_files,
                "result_ids": [result.id],
            },
            "linked_result_ids": [result.id],
            "expected_content": [caption],
        }

    @staticmethod
    def _dedupe_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for request in requests:
            req_id = str(request.get("id") or "").strip()
            if not req_id or req_id in seen:
                continue
            seen.add(req_id)
            deduped.append(request)
        return deduped

    @staticmethod
    def _workflow_issues_from_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for item in requests:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").strip() != "blocked":
                continue
            issues.append(
                {
                    "code": "FIGURE_REQUEST_BLOCKED",
                    "severity": "warning",
                    "stage": "visualization_planner",
                    "reason": str(item.get("blocked_reason") or "blocked").strip(),
                    "request_id": str(item.get("id") or "").strip(),
                    "action": "Use fallback overview or provide renderer/source data.",
                }
            )
        return issues

    @staticmethod
    def discover_tabular_sources(work_dir: str) -> list[str]:
        root = Path(work_dir)
        sources: list[str] = []
        for folder in (root / "data", root / "inputs", root):
            if not folder.exists() or not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                    continue
                try:
                    sources.append(path.relative_to(root).as_posix())
                except Exception:
                    sources.append(path.name)
        return list(dict.fromkeys(sources))
