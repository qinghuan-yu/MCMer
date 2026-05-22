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
        "peak_valley_annotation",
        "residual_plot",
        "spectrum_curve",
    }

    @staticmethod
    def result_overview_requests(
        result_registry: dict[str, Any],
        chart_language: str,
        default_source_data: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        source_data = default_source_data or ["result_registry.json"]
        results = normalize_verified_results(result_registry)
        if not results:
            return []
        anchor = results[0]
        request = result_overview_request(
            result=anchor,
            chart_language=chart_language,
            data_files=anchor.data_artifacts or source_data,
        )
        request["id"] = f"fig_{RESULT_OVERVIEW_ROLE}"
        request["problem_section"] = "paper"
        request["subproblem_id"] = "paper"
        request["linked_result_ids"] = [result.id for result in results if result.id][:12]
        request["depends_on"]["result_ids"] = list(request["linked_result_ids"])
        request["purpose"] = canonical_caption_for_role(RESULT_OVERVIEW_ROLE, chart_language)
        return [request]

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

        requests = cls._limit_result_overview_requests(requests)

        reevaluated = FigurePlanBuilder.reevaluate_requests(
            requests=requests,
            chart_language=chart_language,
            work_dir=work_dir,
        ) if requests else []
        reevaluated = cls._limit_result_overview_requests(reevaluated)
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
            inferred_views = cls._infer_views_for_result(result, source_data)
            if required_views == [RESULT_OVERVIEW_ROLE] and inferred_views:
                required_views = inferred_views
            else:
                for view in inferred_views:
                    if view not in required_views and view not in candidate_views:
                        candidate_views.append(view)
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
                data_files = cls._data_files_for_view(
                    view=view,
                    result_data_files=result.data_artifacts,
                    default_source_data=source_data,
                )
                request = cls._request_for_view(
                    result=result,
                    view_type=view,
                    chart_language=chart_language,
                    data_files=data_files,
                    required=view != RESULT_OVERVIEW_ROLE or not any(item != RESULT_OVERVIEW_ROLE for item in normalized_views),
                )
                if view in inferred_views:
                    request["inferred_from_result_context"] = True
                requests.append(request)
        return cls._limit_result_overview_requests(
            cls._collapse_inferred_domain_requests(cls._dedupe_requests(requests))
        )

    @staticmethod
    def _infer_views_for_result(result: Any, source_data: list[str]) -> list[str]:
        context_parts = [
            getattr(result, "id", ""),
            getattr(result, "section", ""),
            getattr(result, "claim_text", ""),
            getattr(result, "result_type", ""),
            " ".join(getattr(result, "data_artifacts", []) or []),
            " ".join(source_data),
        ]
        context = " ".join(str(item or "") for item in context_parts).lower()
        spectral_terms = (
            "spectrum",
            "wavenumber",
            "reflectance",
            "interference",
            "thickness",
            "wavelength",
            "sic",
            "silicon carbide",
            "光谱",
            "波数",
            "反射率",
            "干涉",
            "厚度",
            "波长",
            "碳化硅",
            "外延",
        )
        if any(term in context for term in spectral_terms):
            return ["spectrum_curve", "peak_valley_annotation"]
        return []

    @staticmethod
    def _data_files_for_view(
        view: str,
        result_data_files: list[str],
        default_source_data: list[str],
    ) -> list[str]:
        result_files = [str(item).strip().replace("\\", "/") for item in (result_data_files or []) if str(item).strip()]
        defaults = [str(item).strip().replace("\\", "/") for item in (default_source_data or []) if str(item).strip()]
        if view in {"spectrum_curve", "peak_valley_annotation"}:
            measurement_files = [
                item for item in defaults
                if item.lower().endswith((".csv", ".xlsx", ".xls"))
                and "result" not in Path(item).name.lower()
                and "summary" not in Path(item).name.lower()
                and "final" not in Path(item).name.lower()
            ]
            if measurement_files:
                return measurement_files
        return result_files or defaults or ["result_registry.json"]

    @staticmethod
    def _collapse_inferred_domain_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collapse_roles = {"spectrum_curve", "peak_valley_annotation"}
        output: list[dict[str, Any]] = []
        grouped: dict[str, dict[str, Any]] = {}
        linked: dict[str, list[str]] = {role: [] for role in collapse_roles}
        sources: dict[str, list[str]] = {role: [] for role in collapse_roles}

        for item in requests:
            if not isinstance(item, dict):
                continue
            role = normalize_figure_role(item.get("semantic_role") or item.get("role"))
            if role not in collapse_roles or not bool(item.get("inferred_from_result_context")):
                output.append(item)
                continue
            if role not in grouped:
                grouped[role] = dict(item)
            for rid in item.get("linked_result_ids", []) or []:
                text = str(rid).strip()
                if text:
                    linked[role].append(text)
            for src in VisualizationPlannerSkill._coerce_text_list(item.get("required_data") or item.get("source_data")):
                text = str(src).strip().replace("\\", "/")
                if text:
                    sources[role].append(text)

        for role, request in grouped.items():
            request["id"] = f"fig_{role}"
            request["problem_section"] = "paper"
            request["subproblem_id"] = "paper"
            request["required"] = True
            request["must_include_in_paper"] = True
            request["linked_result_ids"] = list(dict.fromkeys(linked[role]))[:12]
            request["required_data"] = list(dict.fromkeys(sources[role]))
            request.setdefault("depends_on", {})["result_ids"] = list(request["linked_result_ids"])
            request.setdefault("depends_on", {})["data_files"] = list(request["required_data"])
            output.append(request)
        return output

    @staticmethod
    def _limit_result_overview_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep at most one overview fallback and do not let it crowd out real views."""
        overview: dict[str, Any] | None = None
        output: list[dict[str, Any]] = []
        has_required_specific = any(
            isinstance(item, dict)
            and normalize_figure_role(item.get("semantic_role") or item.get("role")) != RESULT_OVERVIEW_ROLE
            and bool(item.get("required", True))
            and str(item.get("status") or "").strip() != "blocked"
            and not str(item.get("blocked_reason") or "").strip()
            for item in requests
        )
        linked_ids: list[str] = []
        source_data: list[str] = []
        for item in requests:
            if not isinstance(item, dict):
                continue
            role = normalize_figure_role(item.get("semantic_role") or item.get("role"))
            if role != RESULT_OVERVIEW_ROLE:
                output.append(item)
                continue
            if overview is None:
                overview = dict(item)
            for rid in item.get("linked_result_ids", []) or []:
                text = str(rid).strip()
                if text:
                    linked_ids.append(text)
            for src in VisualizationPlannerSkill._coerce_text_list(item.get("required_data") or item.get("source_data")):
                text = str(src).strip()
                if text:
                    source_data.append(text)
        if overview is not None:
            overview["id"] = f"fig_{RESULT_OVERVIEW_ROLE}"
            overview["problem_section"] = "paper"
            overview["subproblem_id"] = "paper"
            overview["required"] = not has_required_specific
            overview["must_include_in_paper"] = not has_required_specific
            if linked_ids:
                overview["linked_result_ids"] = list(dict.fromkeys(linked_ids))[:12]
                overview.setdefault("depends_on", {})["result_ids"] = list(overview["linked_result_ids"])
            if source_data:
                overview["required_data"] = list(dict.fromkeys(source_data))
                overview.setdefault("depends_on", {})["data_files"] = list(overview["required_data"])
            output.append(overview)
        return output

    @staticmethod
    def _coerce_text_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, tuple):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        return []

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
            "spectrum_curve": [],
            "peak_valley_annotation": [],
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
