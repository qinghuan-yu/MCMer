"""FigurePlan builder for structured figure planning.

This module upgrades figure decision logic from keyword-only heuristics to a
multi-signal planner:
- problem/solve_spec structure signals
- domain template matching
- lightweight data-shape/file signals
- rule-based backfill when figure requests are missing
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.common_utils import save_json


PLACEHOLDER_RESULT_IDS = {
    "verified_series",
    "result_series",
    "placeholder_result",
    "todo_result",
}


FIGURE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "trajectory_shielding": {
        "renderer": "render_trajectory_shielding_2d",
        "requires_data": False,
        "requires_verified_result": True,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "distance_time_curve": {
        "renderer": "render_distance_time_curve",
        "requires_data": False,
        "requires_verified_result": True,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "geometry_schematic": {
        "renderer": "render_geometry_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "physical_principle_schematic": {
        "renderer": "render_geometry_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "optical_path_diagram": {
        "renderer": "render_geometry_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "timeline_schematic": {
        "renderer": "render_timeline_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "trajectory_overview": {
        "renderer": "render_geometry_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "optimization_variable_diagram": {
        "renderer": "render_geometry_schematic",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "algorithm_flowchart": {
        "renderer": "render_algorithm_flowchart",
        "requires_data": False,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "spectrum_curve": {
        "renderer": "render_spectrum_curve",
        "requires_data": True,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
    "peak_valley_annotation": {
        "renderer": "render_peak_valley_annotation",
        "requires_data": True,
        "requires_verified_result": False,
        "supports_languages": ["Simplified Chinese", "English"],
    },
}


def _norm_text(value: object) -> str:
    return str(value or "").strip()


def _join_texts(*parts: object) -> str:
    return "\n".join(_norm_text(p) for p in parts if _norm_text(p))


def _solve_spec_subproblems(solve_spec: dict[str, Any]) -> list[dict[str, Any]]:
    sub = solve_spec.get("subproblems", []) if isinstance(solve_spec, dict) else []
    return [item for item in sub if isinstance(item, dict)] if isinstance(sub, list) else []


@dataclass
class FigureNeedResult:
    requires_figures: bool
    requires_result_figures: bool
    requires_explanatory_figures: bool
    confidence: float
    reasons: list[str]
    detected_needs: list[str]
    domain_type: str


class FigureNeedDetector:
    """Multi-source figure-need detector."""

    OPTICAL_INTERFERENCE_RE = re.compile(
        r"外延层|衬底|干涉|多光束|光程差|波数|反射率|峰|谷|厚度公式|optical|interference|epitaxial|substrate",
        re.IGNORECASE,
    )

    EXPLANATORY_RE = re.compile(
        r"物理模型|几何|空间关系|轨迹|路径|时间过程|流程|推导|原理|"
        r"physical|geometry|trajectory|path|timeline|schematic|flow|derivation",
        re.IGNORECASE,
    )

    RESULT_RE = re.compile(
        r"测量数据|时间序列|优化|拟合|敏感性|比较|光谱|峰谷|反射率|"
        r"measurement|time\s*series|optimization|fit|sensitivity|comparison|spectrum|peak|valley|reflectance",
        re.IGNORECASE,
    )

    @classmethod
    def detect(
        cls,
        question: str,
        solve_spec: dict[str, Any],
        problem_facts: dict[str, Any] | None,
        work_dir: str,
    ) -> FigureNeedResult:
        reasons: list[str] = []
        detected_needs: list[str] = []

        combined = _join_texts(question, json.dumps(solve_spec or {}, ensure_ascii=False), json.dumps(problem_facts or {}, ensure_ascii=False))

        domain_type = "general"
        if cls.OPTICAL_INTERFERENCE_RE.search(combined):
            domain_type = "optical_thin_film_interference"
            reasons.append("domain_template_optical_thin_film_interference")
            detected_needs.extend([
                "optical_path_diagram",
                "spectrum_curve",
                "peak_valley_annotation",
                "algorithm_flowchart",
            ])

        # SolveSpec structure signals
        for sub in _solve_spec_subproblems(solve_spec):
            method = _norm_text(sub.get("method"))
            steps = sub.get("steps", [])
            expected_outputs = sub.get("expected_outputs", [])
            input_files = sub.get("input_files", [])

            if method:
                if cls.EXPLANATORY_RE.search(method):
                    reasons.append("solve_spec_method_requires_explanatory_figure")
                    detected_needs.append("physical_principle_schematic")
                if cls.RESULT_RE.search(method):
                    reasons.append("solve_spec_method_requires_result_figure")
                    detected_needs.append("spectrum_curve")

            if isinstance(input_files, list) and input_files:
                reasons.append("solve_spec_has_input_files")
                detected_needs.append("result_data_plot")

            if isinstance(steps, list) and any("拟合" in _norm_text(step) or "fit" in _norm_text(step).lower() for step in steps):
                reasons.append("solve_spec_has_fitting_step")
                detected_needs.extend(["fitting_curve", "residual_plot"])

            if isinstance(expected_outputs, list) and any("厚度" in _norm_text(x) or "thickness" in _norm_text(x).lower() for x in expected_outputs):
                reasons.append("solve_spec_expected_thickness_output")
                detected_needs.append("algorithm_flowchart")

        # Data shape/file signals (lightweight)
        data_reasons, data_needs = cls._inspect_input_shapes(work_dir)
        reasons.extend(data_reasons)
        detected_needs.extend(data_needs)

        unique_needs = list(dict.fromkeys(detected_needs))
        unique_reasons = list(dict.fromkeys(reasons))

        requires_explanatory = any(
            n in unique_needs
            for n in {
                "physical_principle_schematic",
                "geometry_schematic",
                "optical_path_diagram",
                "timeline_schematic",
                "algorithm_flowchart",
            }
        )
        requires_result = any(
            n in unique_needs
            for n in {
                "spectrum_curve",
                "peak_valley_annotation",
                "fitting_curve",
                "residual_plot",
                "result_data_plot",
            }
        )

        requires_figures = requires_explanatory or requires_result
        confidence = min(0.99, 0.45 + 0.07 * len(unique_reasons) + (0.12 if domain_type != "general" else 0.0))
        if not requires_figures:
            confidence = 0.2

        return FigureNeedResult(
            requires_figures=requires_figures,
            requires_result_figures=requires_result,
            requires_explanatory_figures=requires_explanatory,
            confidence=round(confidence, 4),
            reasons=unique_reasons,
            detected_needs=unique_needs,
            domain_type=domain_type,
        )

    @classmethod
    def _inspect_input_shapes(cls, work_dir: str) -> tuple[list[str], list[str]]:
        reasons: list[str] = []
        needs: list[str] = []
        root = Path(work_dir)
        candidates: list[Path] = []
        for folder in (root / "data", root / "inputs", root):
            if not folder.exists() or not folder.is_dir():
                continue
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in {".csv", ".xlsx", ".xls"}:
                    candidates.append(f)

        if not candidates:
            return reasons, needs

        reasons.append("problem_has_measurement_data")

        for path in candidates[:8]:
            suffix = path.suffix.lower()
            name = path.name.lower()
            if suffix in {".xlsx", ".xls"}:
                reasons.append("tabular_excel_input_detected")
                needs.append("comparison_bar")
            if "spectrum" in name or "光谱" in name or "波数" in name:
                reasons.append("spectral_file_name_detected")
                needs.extend(["spectrum_curve", "peak_valley_annotation"])

            if suffix == ".csv":
                try:
                    with path.open("r", encoding="utf-8", newline="") as fp:
                        reader = csv.reader(fp)
                        header = next(reader, [])
                except Exception:
                    header = []
                header_text = " ".join(_norm_text(h).lower() for h in header)
                if any(token in header_text for token in ["time", "时间", "t_"]):
                    reasons.append("time_series_column_detected")
                    needs.append("timeline_schematic")
                if any(token in header_text for token in ["wavenumber", "波数", "reflectance", "反射率"]):
                    reasons.append("spectrum_columns_detected")
                    needs.extend(["spectrum_curve", "peak_valley_annotation"])
                if any(token in header_text for token in ["x", "y", "z", "坐标"]):
                    reasons.append("spatial_columns_detected")
                    needs.append("geometry_schematic")

        return list(dict.fromkeys(reasons)), list(dict.fromkeys(needs))


class FigurePlanBuilder:
    """Build and persist figure_plan.json as the source of truth."""

    EXPLANATORY_TEMPLATE_BY_NEED = {
        "physical_principle_schematic": ("physical_principle_schematic", True),
        "optical_path_diagram": ("optical_path_diagram", True),
        "geometry_schematic": ("geometry_schematic", True),
        "timeline_schematic": ("timeline_schematic", True),
        "algorithm_flowchart": ("algorithm_flowchart", True),
        "trajectory_overview": ("trajectory_overview", True),
        "optimization_variable_diagram": ("optimization_variable_diagram", False),
    }

    RESULT_TEMPLATE_BY_NEED = {
        "spectrum_curve": "spectrum_curve",
        "peak_valley_annotation": "peak_valley_annotation",
        "fitting_curve": "fitting_curve",
        "residual_plot": "residual_plot",
        "result_data_plot": "comparison_bar",
        "comparison_bar": "comparison_bar",
    }

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
        requests = cls._seed_from_existing_requests(solve_spec, chart_language)
        available_data_files = cls._discover_input_files(work_dir)
        verified_result_ids = cls._load_verified_result_ids(work_dir)

        # Rule-based backfill if detector says figures are needed but requests are empty/incomplete.
        if need.requires_figures:
            requests = cls._backfill_requests(
                requests=requests,
                solve_spec=solve_spec,
                chart_language=chart_language,
                domain_type=need.domain_type,
                detected_needs=need.detected_needs,
                available_data_files=available_data_files,
            )

        requests = cls._apply_capability_constraints(
            requests=requests,
            chart_language=chart_language,
            available_data_files=available_data_files,
            verified_result_ids=verified_result_ids,
        )

        required_count = sum(1 for item in requests if isinstance(item, dict) and bool(item.get("required", True)))
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
            "requires_figures": required_count > 0,
            "requires_result_figures": requires_result_figures,
            "requires_explanatory_figures": requires_explanatory_figures,
            "confidence": need.confidence,
            "reasons": need.reasons,
            "detected_needs": need.detected_needs,
            "domain_type": need.domain_type,
            "planning_basis": cls._planning_basis_from_reasons(need.reasons),
            "required_count": required_count,
            "recommended_count": sum(1 for item in requests if isinstance(item, dict) and str(item.get("status") or "") == "recommended"),
            "blocked_count": sum(1 for item in requests if isinstance(item, dict) and str(item.get("status") or "") == "blocked"),
            "figure_requests": requests,
        }

        return plan

    @classmethod
    def _discover_input_files(cls, work_dir: str) -> list[str]:
        root = Path(work_dir)
        discovered: list[str] = []
        for folder in (root / "inputs", root / "data", root):
            if not folder.exists() or not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json"}:
                    continue
                try:
                    rel = path.relative_to(root).as_posix()
                except Exception:
                    rel = path.name
                discovered.append(rel)
        return list(dict.fromkeys(discovered))

    @classmethod
    def _load_verified_result_ids(cls, work_dir: str) -> set[str]:
        path = Path(work_dir) / "result_registry.json"
        if not path.exists():
            return set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(payload, dict):
            return set()
        verified = payload.get("verified_results", [])
        if not isinstance(verified, list):
            return set()
        return {
            str(item.get("id") or "").strip()
            for item in verified
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    @classmethod
    def _apply_capability_constraints(
        cls,
        requests: list[dict[str, Any]],
        chart_language: str,
        available_data_files: list[str],
        verified_result_ids: set[str],
    ) -> list[dict[str, Any]]:
        available_set = {str(item).strip().replace("\\", "/") for item in available_data_files if str(item).strip()}
        evaluated: list[dict[str, Any]] = []

        for raw in requests:
            if not isinstance(raw, dict):
                continue
            req = dict(raw)
            role = _norm_text(req.get("semantic_role") or req.get("role")).lower()
            kind = _norm_text(req.get("figure_kind") or "result_figure").lower()
            required = bool(req.get("required", True))
            req.setdefault("figure_kind", kind or "result_figure")
            req["semantic_role"] = role
            req.setdefault("role", role)

            depends_on = req.get("depends_on", {}) if isinstance(req.get("depends_on"), dict) else {}
            data_files = depends_on.get("data_files", []) if isinstance(depends_on.get("data_files"), list) else []
            data_files_list = [str(item).strip().replace("\\", "/") for item in data_files if str(item).strip()]

            linked_ids = req.get("linked_result_ids", [])
            if not isinstance(linked_ids, list):
                linked_ids = []
            if not linked_ids:
                linked_ids = depends_on.get("result_ids", []) if isinstance(depends_on.get("result_ids"), list) else []
            linked_ids_list = [str(item).strip() for item in linked_ids if str(item).strip()]
            placeholder_ids = {item for item in linked_ids_list if item.lower() in PLACEHOLDER_RESULT_IDS}
            linked_ids_list = [item for item in linked_ids_list if item.lower() not in PLACEHOLDER_RESULT_IDS]

            req["linked_result_ids"] = linked_ids_list
            depends_on["result_ids"] = linked_ids_list
            depends_on["data_files"] = data_files_list
            req["depends_on"] = depends_on
            req["required_data"] = data_files_list

            capability = FIGURE_CAPABILITIES.get(role)
            blocked_reason = ""

            if not capability:
                blocked_reason = f"unsupported_renderer:{role or 'unknown'}"
            elif chart_language not in (capability.get("supports_languages") or []):
                blocked_reason = f"unsupported_chart_language:{chart_language}"
            elif bool(capability.get("requires_data")) and kind == "result_figure":
                candidates = [item for item in data_files_list if item != "result_registry.json"]
                if not candidates:
                    blocked_reason = "missing_source_data"
                elif not any(item in available_set for item in candidates):
                    blocked_reason = "missing_source_data"
            if not blocked_reason and bool(capability and capability.get("requires_verified_result")) and kind == "result_figure":
                if placeholder_ids:
                    blocked_reason = "placeholder_linked_result_ids"
                elif not linked_ids_list:
                    blocked_reason = "missing_verified_result_binding"
                elif verified_result_ids and any(item not in verified_result_ids for item in linked_ids_list):
                    blocked_reason = "linked_result_not_verified"

            if blocked_reason:
                req["required"] = False
                req["must_include_in_paper"] = False
                req["recommended"] = True
                req["status"] = "blocked"
                req["blocked_reason"] = blocked_reason
                req["feasible"] = False
            else:
                req["feasible"] = True
                if required:
                    req["required"] = True
                    req["must_include_in_paper"] = bool(req.get("must_include_in_paper", True))
                    req["recommended"] = False
                    req["status"] = "required"
                    req.pop("blocked_reason", None)
                else:
                    req["required"] = False
                    req["must_include_in_paper"] = False
                    req["recommended"] = True
                    req["status"] = "recommended"
                    req.pop("blocked_reason", None)
            evaluated.append(req)

        return evaluated

    @classmethod
    def save(cls, work_dir: str, figure_plan: dict[str, Any]) -> str:
        path = str(Path(work_dir) / "figure_plan.json")
        save_json(figure_plan, path)
        return path

    @classmethod
    def _planning_basis_from_reasons(cls, reasons: list[str]) -> list[str]:
        basis: list[str] = []
        mapping = {
            "problem_has_measurement_data": "problem_has_measurement_data",
            "solve_spec_has_input_files": "problem_has_measurement_data",
            "solve_spec_method_requires_explanatory_figure": "problem_has_physical_model",
            "solve_spec_method_requires_result_figure": "problem_has_algorithmic_process",
            "spectrum_columns_detected": "problem_has_measurement_data",
            "time_series_column_detected": "problem_has_algorithmic_process",
            "domain_template_optical_thin_film_interference": "problem_has_formula_derivation",
        }
        for reason in reasons:
            value = mapping.get(reason)
            if value:
                basis.append(value)
        return list(dict.fromkeys(basis))

    @classmethod
    def _seed_from_existing_requests(cls, solve_spec: dict[str, Any], chart_language: str) -> list[dict[str, Any]]:
        existing = solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []
        if not isinstance(existing, list):
            return []
        seeded: list[dict[str, Any]] = []
        for item in existing:
            if not isinstance(item, dict):
                continue
            payload = dict(item)
            payload.setdefault("chart_language", chart_language)
            payload.setdefault("language", chart_language)
            payload.setdefault("figure_kind", "result_figure")
            payload.setdefault("semantic_role", _norm_text(payload.get("role")) or "result_visualization")
            payload.setdefault("must_include_in_paper", bool(payload.get("required", True)))
            payload.setdefault("depends_on", {
                "model_objects": [],
                "formulas": [],
                "data_files": payload.get("required_data", [] if not isinstance(payload.get("required_data"), list) else payload.get("required_data", [])),
                "result_ids": payload.get("linked_result_ids", []),
            })
            seeded.append(payload)
        return seeded

    @classmethod
    def _backfill_requests(
        cls,
        requests: list[dict[str, Any]],
        solve_spec: dict[str, Any],
        chart_language: str,
        domain_type: str,
        detected_needs: list[str],
        available_data_files: list[str],
    ) -> list[dict[str, Any]]:
        existing_ids = {
            _norm_text(item.get("id"))
            for item in requests
            if isinstance(item, dict) and _norm_text(item.get("id"))
        }

        subproblems = _solve_spec_subproblems(solve_spec)
        if not subproblems:
            subproblems = [{"id": "problem_1", "objective": _norm_text(solve_spec.get("summary")) or "problem"}]

        def _sub_id(index: int, sub: dict[str, Any]) -> str:
            return _norm_text(sub.get("id")) or f"problem_{index + 1}"

        def _resolve_data_files(sub: dict[str, Any], prefer_spectrum: bool = False) -> list[str]:
            input_files = sub.get("input_files", [])
            names = [str(item).strip() for item in input_files if str(item).strip()] if isinstance(input_files, list) else []
            matched: list[str] = []
            for name in names:
                name_base = Path(name).name.lower()
                for rel in available_data_files:
                    if Path(rel).name.lower() == name_base:
                        matched.append(rel)
            if not matched:
                matched = [rel for rel in available_data_files if rel.lower().endswith((".csv", ".xlsx", ".xls"))]
            if prefer_spectrum:
                spectral = [rel for rel in matched if any(token in rel.lower() for token in ["spectrum", "光谱", "波数", "reflectance"])]
                if spectral:
                    matched = spectral
            if "result_registry.json" not in matched:
                matched.append("result_registry.json")
            return list(dict.fromkeys(matched))

        def _mk_request(
            req_id: str,
            subproblem_id: str,
            figure_kind: str,
            semantic_role: str,
            purpose: str,
            required: bool,
            depends_on: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "id": req_id,
                "problem_section": subproblem_id,
                "subproblem_id": subproblem_id,
                "figure_kind": figure_kind,
                "semantic_role": semantic_role,
                "role": semantic_role,
                "purpose": purpose,
                "required": required,
                "must_include_in_paper": bool(required),
                "chart_language": chart_language,
                "language": chart_language,
                "depends_on": depends_on,
                "required_data": list(depends_on.get("data_files", []) or []),
                "linked_result_ids": list(depends_on.get("result_ids", []) or []),
                "expected_content": [purpose],
            }

        for i, sub in enumerate(subproblems):
            sid = _sub_id(i, sub)
            objective = _norm_text(sub.get("objective")) or sid

            # Domain-first template for optical interference
            if domain_type == "optical_thin_film_interference":
                optical_templates = [
                    ("optical_path_diagram", "explanatory_figure", "说明干涉光程差关系", True),
                    ("spectrum_curve", "result_figure", "展示反射率-波数光谱曲线", True),
                    ("peak_valley_annotation", "result_figure", "标注峰谷并支撑厚度估计", True),
                    ("algorithm_flowchart", "explanatory_figure", "说明厚度求解与复核流程", True),
                ]
                for role, kind, purpose, required in optical_templates:
                    req_id = f"fig_{sid}_{role}"
                    if req_id in existing_ids:
                        continue
                    existing_ids.add(req_id)
                    requests.append(
                        _mk_request(
                            req_id=req_id,
                            subproblem_id=sid,
                            figure_kind=kind,
                            semantic_role=role,
                            purpose=f"{objective}：{purpose}",
                            required=required,
                            depends_on={
                                "model_objects": ["epitaxial_layer", "substrate", "incident_beam", "reflected_beam"] if kind == "explanatory_figure" else [],
                                "formulas": ["2 n d cos(theta) = m lambda"] if role in {"optical_path_diagram", "peak_valley_annotation"} else [],
                                "data_files": ["result_registry.json"] if kind == "explanatory_figure" else _resolve_data_files(sub, prefer_spectrum=True),
                                "result_ids": [] if kind == "explanatory_figure" else ["thickness_estimate", "peak_wavenumbers"],
                            },
                        )
                    )

            # Generic detected-needs backfill
            for need in detected_needs:
                if need in cls.EXPLANATORY_TEMPLATE_BY_NEED:
                    role, required = cls.EXPLANATORY_TEMPLATE_BY_NEED[need]
                    req_id = f"fig_{sid}_{role}"
                    if req_id in existing_ids:
                        continue
                    existing_ids.add(req_id)
                    requests.append(
                        _mk_request(
                            req_id=req_id,
                            subproblem_id=sid,
                            figure_kind="explanatory_figure",
                            semantic_role=role,
                            purpose=f"{objective}：{role}",
                            required=required,
                            depends_on={
                                "model_objects": ["model_core"],
                                "formulas": [],
                                "data_files": ["result_registry.json"],
                                "result_ids": [],
                            },
                        )
                    )
                elif need in cls.RESULT_TEMPLATE_BY_NEED:
                    role = cls.RESULT_TEMPLATE_BY_NEED[need]
                    req_id = f"fig_{sid}_{role}"
                    if req_id in existing_ids:
                        continue
                    existing_ids.add(req_id)
                    requests.append(
                        _mk_request(
                            req_id=req_id,
                            subproblem_id=sid,
                            figure_kind="result_figure",
                            semantic_role=role,
                            purpose=f"{objective}：{role}",
                            required=True,
                            depends_on={
                                "model_objects": [],
                                "formulas": [],
                                "data_files": _resolve_data_files(sub, prefer_spectrum=(role in {"spectrum_curve", "peak_valley_annotation"})),
                                "result_ids": [],
                            },
                        )
                    )

        return requests
