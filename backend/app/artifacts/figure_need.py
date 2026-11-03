"""figure need responsibilities."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
                    detected_needs.append("result_data_plot")

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
                header_tokens = {
                    token
                    for h in header
                    for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", _norm_text(h).lower())
                    if token
                }
                if any(token in header_text for token in ["time", "时间", "t_"]):
                    reasons.append("time_series_column_detected")
                    needs.append("timeline_schematic")
                if any(token in header_text for token in ["wavenumber", "波数", "reflectance", "反射率"]):
                    reasons.append("spectrum_columns_detected")
                    needs.extend(["spectrum_curve", "peak_valley_annotation"])

                spatial_tokens = {"x", "y", "z", "x_coord", "y_coord", "z_coord", "坐标"}
                if len(header_tokens.intersection(spatial_tokens)) >= 2:
                    reasons.append("spatial_columns_detected")
                    needs.append("geometry_schematic")

        return list(dict.fromkeys(reasons)), list(dict.fromkeys(needs))
