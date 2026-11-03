"""chart support responsibilities."""

from __future__ import annotations

from app.core.figure_requests import ensure_figure_requests_in_solve_spec, figure_requests_for_subproblem, required_figure_requests
import re
import shutil
from pathlib import Path
from app.artifacts.figure_plan import FigurePlanBuilder
from app.core.skills.visualization_planner import VisualizationPlannerSkill
from app.core.workflow_budget import WorkflowBudget
from app.utils.file_io import load_json
from app.utils.figure_artifacts import load_figure_manifest, normalize_artifact_path, save_figure_manifest
from app.utils.log_util import logger
from app.artifacts.artifact_evidence import generated_file_path as _generated_file_path
from app.utils.figure_artifacts import is_image_path as _is_image_path


def _infer_figure_role(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ["轨迹", "trajectory"]):
        return "trajectory_shielding"
    if any(token in lowered for token in ["收敛", "convergence", "优化"]):
        return "optimization_convergence"
    if any(token in lowered for token in ["距离", "distance", "时间", "time"]):
        return "distance_time_curve"
    if any(token in lowered for token in ["敏感性", "sensitivity"]):
        return "sensitivity_analysis"
    return "result_visualization"


def _compat_figure_requests_from_solve_spec(
    solve_spec: dict[str, object],
    chart_language: str,
) -> list[dict[str, object]]:
    """Build legacy figure requests on a copy so solve_spec remains artifact-light."""
    if not isinstance(solve_spec, dict):
        return []
    compat_spec = dict(solve_spec)
    return ensure_figure_requests_in_solve_spec(compat_spec, chart_language)


def _required_figure_requests_from_plan(
    figure_plan: dict[str, object] | None,
    solve_spec: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Return required requests from figure_plan, falling back to legacy solve_spec."""
    requests = figure_plan.get("figure_requests", []) if isinstance(figure_plan, dict) else []
    if isinstance(requests, list):
        planned = [
            req
            for req in requests
            if isinstance(req, dict)
            and bool(req.get("required", True))
            and str(req.get("status") or "").strip().lower() != "blocked"
            and req.get("feasible", True) is not False
        ]
        if planned:
            return planned
    return required_figure_requests(solve_spec or {})


def _figure_requests_for_subproblem_from_plan(
    figure_plan: dict[str, object] | None,
    subproblem_id: str,
    solve_spec: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Return scoped requests from figure_plan, falling back to legacy solve_spec."""
    requests = figure_plan.get("figure_requests", []) if isinstance(figure_plan, dict) else []
    sid = str(subproblem_id or "").strip()
    scoped: list[dict[str, object]] = []
    if sid and isinstance(requests, list):
        for item in requests:
            if not isinstance(item, dict):
                continue
            if str(item.get("subproblem_id") or item.get("problem_section") or "").strip() == sid:
                scoped.append(item)
        if scoped:
            return scoped
    return figure_requests_for_subproblem(solve_spec or {}, sid)


def _figure_plan_expects_figures(
    figure_plan: dict[str, object] | None,
    fallback: bool = False,
) -> bool:
    if not isinstance(figure_plan, dict):
        return fallback
    for key in (
        "requires_figures",
        "requires_result_figures",
        "requires_explanatory_figures",
        "requires_figures_by_problem",
    ):
        if bool(figure_plan.get(key)):
            return True
    requests = figure_plan.get("figure_requests", [])
    return bool(isinstance(requests, list) and any(isinstance(item, dict) for item in requests))


def _figure_plan_bool(
    figure_plan: dict[str, object] | None,
    key: str,
    fallback: bool = False,
) -> bool:
    if isinstance(figure_plan, dict) and key in figure_plan:
        return bool(figure_plan.get(key))
    return fallback


def _expected_figures_from_plan_or_legacy(
    figure_plan: dict[str, object] | None,
    solve_spec: dict[str, object],
    question: str,
    breakdown: str,
) -> bool:
    """Prefer figure_plan as the visualization requirement source of truth."""
    legacy_expected = _solve_spec_expects_figures(solve_spec, question, breakdown)
    return _figure_plan_expects_figures(figure_plan, legacy_expected)


def _infer_figure_requirement_flags(
    solve_spec: dict[str, object],
    question: str,
    breakdown: str,
) -> tuple[bool, bool, bool]:
    """Resolve requires_result/explanatory/any flags from solve_spec + heuristics."""
    if isinstance(solve_spec, dict):
        req_result = solve_spec.get("requires_result_figures")
        req_expl = solve_spec.get("requires_explanatory_figures")
        req_any = solve_spec.get("requires_figures")
        if isinstance(req_result, bool) and isinstance(req_expl, bool):
            combined = req_result or req_expl
            if isinstance(req_any, bool):
                combined = bool(req_any or combined)
            return req_result, req_expl, combined

    text = "\n".join(filter(None, [question, breakdown]))
    explanatory_kw = re.compile(
        r"几何|轨迹|遮蔽|路径|时间过程|时间区间|优化变量关系|示意图|流程图|时间轴|"
        r"geometry|trajectory|shield|path|timeline|schematic|flowchart|variable\s*diagram",
        re.IGNORECASE,
    )
    result_kw = re.compile(
        r"时间序列|参数扫描|灵敏度|优化过程|收敛|曲线|热力图|"
        r"time\s*series|parameter\s*sweep|sensitivity|optimization\s*process|convergence|curve|heatmap",
        re.IGNORECASE,
    )
    req_expl = bool(explanatory_kw.search(text))
    req_result = bool(result_kw.search(text))
    req_any = req_expl or req_result or bool(_FIGURE_KEYWORDS.search(text))
    return req_result, req_expl, req_any


def _registered_generated_files(work_dir: str, structured_result_files: list[str]) -> set[str]:
    root = Path(work_dir)
    registered: set[str] = set()
    # Include entries from figure_artifacts.json manifest as registered sources
    for manifest_item in load_figure_manifest(work_dir):
        raw = _generated_file_path(manifest_item)
        if raw:
            registered.add(raw)
    for filename in structured_result_files:
        payload = load_json(str(root / filename))
        if not isinstance(payload, dict):
            continue
        generated_files = payload.get("generated_files", [])
        if not isinstance(generated_files, list):
            continue
        for item in generated_files:
            raw = _generated_file_path(item)
            if raw:
                registered.add(raw)
    return registered


def _coerce_numeric_value(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _create_verified_result_summary_figure(
    work_dir: str,
    result_registry: dict[str, object],
    document_language: str,
) -> list[str]:
    """Create a deterministic paper-ready figure when LLM chart repair fails.

    This is a trusted workflow fallback, not a relaxation of the final gate:
    the generated artifact still goes through ``is_verified_paper_figure`` with
    linked_result_ids validated against ``result_registry``.
    """
    verified = result_registry.get("verified_results", []) if isinstance(result_registry, dict) else []
    if not isinstance(verified, list) or not verified:
        return []

    rows: list[tuple[str, str, float, str]] = []
    for entry in verified:
        if not isinstance(entry, dict):
            continue
        result_id = str(entry.get("id") or "").strip()
        if not result_id:
            continue
        value = _coerce_numeric_value(entry.get("value"))
        if value is None:
            continue
        name = str(entry.get("name") or result_id).strip()
        unit = str(entry.get("unit") or "").strip()
        rows.append((result_id, name, value, unit))
        if len(rows) >= 8:
            break

    if not rows:
        return []

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        font_manager._load_fontmanager(try_read_cache=False)
        matplotlib.rcParams["font.family"] = "sans-serif"
        matplotlib.rcParams["font.sans-serif"] = [
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Noto Sans CJK SC",
            "SimHei",
            "Microsoft YaHei",
            "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False

        output_dir = Path(work_dir) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        rel_path = "output/verified_result_summary.png"
        out_path = Path(work_dir) / rel_path

        ids = [row[0] for row in rows]
        names = [row[1] for row in rows]
        values = [row[2] for row in rows]
        labels = [f"{i + 1}" for i in range(len(rows))]

        is_english = document_language == "English"
        title = "Verified Result Summary" if is_english else "已验证结果摘要"
        xlabel = "Result index" if is_english else "结果序号"
        ylabel = "Value" if is_english else "数值"
        note_title = "Linked result IDs" if is_english else "关联结果编号"

        fig, ax = plt.subplots(figsize=(8.8, 5.4))
        ax.bar(labels, values, color="#2f80ed", alpha=0.86)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.28)
        note_lines = [
            f"{idx + 1}. {name[:22]}{'...' if len(name) > 22 else ''}"
            for idx, name in enumerate(names[:6])
        ]
        ax.text(
            1.02,
            0.98,
            note_title + "\n" + "\n".join(note_lines),
            transform=ax.transAxes,
            va="top",
            fontsize=9,
        )
        fig.tight_layout()
        fig.savefig(out_path, dpi=220, bbox_inches="tight")
        plt.close(fig)

        source_files = sorted(
            {
                str(entry.get("source_file") or "").strip()
                for entry in verified
                if isinstance(entry, dict) and entry.get("source_file")
            }
        )
        artifact = {
            "path": rel_path,
            "kind": "figure",
            "paper_ready": True,
            "visible_text_language": "English" if is_english else "Simplified Chinese",
            "chart_language_verified": True,
            "visible_text_audit": [title, xlabel, ylabel, note_title],
            "created_by": "deterministic_renderer",
            "helper_version": 3,
            "is_placeholder": False,
            "figure_role": "verified_result_summary",
            "linked_result_ids": ids,
            "source_data": ["result_registry.json", *source_files[:6]],
            "paper_section": "diagnostics",
            "semantic_verified": False,
            "semantic_role": "verified_result_summary",
            "figure_request_id": "diagnostic_verified_result_summary",
            "subproblem_id": "diagnostics",
            "depicts": ["verified_result_summary"],
            "data_bindings": ["result_registry.json", *source_files[:6]],
        }
        existing = load_figure_manifest(work_dir)
        save_figure_manifest(work_dir, [*existing, artifact])
        logger.info("Created deterministic verified-result summary figure: {}", rel_path)
        return [rel_path]
    except Exception as exc:
        logger.warning("Failed to create deterministic verified-result summary figure: {}", exc)
        return []


def _quarantine_unregistered_output_images(work_dir: str, structured_result_files: list[str]) -> list[str]:
    root = Path(work_dir)
    if not root.exists():
        return []
    registered = _registered_generated_files(work_dir, structured_result_files)
    debug_dir = root / "debug_artifacts"
    moved: list[str] = []
    candidates: list[Path] = []
    for candidate in root.iterdir():
        if candidate.is_file() and _is_image_path(candidate.name):
            candidates.append(candidate)
    for dirname in ("output", "results"):
        folder = root / dirname
        if folder.exists():
            candidates.extend(path for path in folder.rglob("*") if path.is_file() and _is_image_path(path.name))

    for image_path in sorted(candidates):
        rel = image_path.relative_to(root).as_posix()
        if rel in registered or rel.startswith("debug_artifacts/"):
            continue
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / image_path.name
        if target.exists():
            target = debug_dir / f"{image_path.stem}_{len(moved) + 1}{image_path.suffix}"
        shutil.move(str(image_path), str(target))
        moved.append(rel)
    if moved:
        logger.info("Moved unregistered exploratory charts to debug_artifacts: {}", moved)
    return moved


def _normalize_paper_image_references(
    work_dir: str,
    markdown_text: str,
    allowed_images: list[str] | None = None,
) -> str:
    root = Path(work_dir)
    if not markdown_text or not root.exists():
        return markdown_text
    if allowed_images is None:
        return markdown_text

    allowed = {normalize_artifact_path(item) for item in allowed_images if str(item).strip()}
    by_name = {Path(item).name: item for item in allowed}
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def replacement(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        raw_target = normalize_artifact_path(match.group(2).strip().strip("<>"))
        if raw_target in allowed:
            return match.group(0)
        rel = by_name.get(Path(raw_target).name)
        if rel:
            return f"![{alt_text}]({rel})"
        return match.group(0)

    return image_pattern.sub(replacement, markdown_text)


def _refresh_result_driven_figure_plan(
    work_dir: str,
    solve_spec: dict[str, object],
    result_registry: dict[str, object],
    chart_language: str,
    solve_spec_path: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Regenerate figure_plan from verified results without mutating solve_spec."""
    if not isinstance(solve_spec, dict) or not isinstance(result_registry, dict):
        return {}, []
    if not result_registry.get("verified_results"):
        return {}, []

    figure_plan = VisualizationPlannerSkill.build_figure_plan(
        result_registry=result_registry,
        chart_language=chart_language,
        work_dir=work_dir,
        solve_spec=solve_spec,
    )
    FigurePlanBuilder.save(work_dir, figure_plan)
    figure_requests = [
        item for item in figure_plan.get("figure_requests", [])
        if isinstance(item, dict)
    ]
    return figure_plan, figure_requests


def _should_run_chart_agent(
    workflow_mode: str,
    result_registry: dict[str, object],
    solver_images: list[str],
    budget: WorkflowBudget,
) -> bool:
    verified_count = len(result_registry.get("verified_results", []) or [])
    if verified_count > 0:
        return workflow_mode == "strict" or not solver_images
    if not budget.allow_chart_recovery_without_verified_results:
        return False
    recoverable_entries = (
        list(result_registry.get("blocked_results", []) or [])
        + list(result_registry.get("warning_results", []) or [])
    )
    has_registered_artifacts = any(
        isinstance(entry, dict) and (entry.get("source_data") or entry.get("generated_files"))
        for entry in recoverable_entries
    )
    return bool(solver_images or has_registered_artifacts)


_FIGURE_KEYWORDS = re.compile(
    r"(?:图表|作图|绘图|制图|画图|插图|示意图|流程图|架构图|拓扑图|"
    r"折线图|散点图|柱状图|饼图|箱线图|热力图|等高线图|雷达图|直方图|"
    r"figure|chart|plot|graph|diagram|visualization|visuali[sz]e|"
    r"scatter|histogram|heatmap|contour|boxplot|bar\s*chart|pie\s*chart|"
    r"折线|柱状|散点|热力|等高线|箱线|雷达|可视化)",
    re.IGNORECASE,
)


def _solve_spec_expects_figures(
    solve_spec: dict[str, object],
    question: str,
    breakdown: str,
) -> bool:
    """Heuristic check: does the task context indicate figures are expected?

    Resolution order:
    1. FigurePlan-derived requirement flags in solve_spec (preferred).
    2. Legacy requires/expected bool fields.
    3. Keyword matching on question + breakdown (legacy fallback only).
    """
    if isinstance(solve_spec, dict):
        by_problem = solve_spec.get("requires_figures_by_problem")
        if isinstance(by_problem, bool):
            return by_problem

        req_result = solve_spec.get("requires_result_figures")
        req_expl = solve_spec.get("requires_explanatory_figures")
        if isinstance(req_result, bool) or isinstance(req_expl, bool):
            return bool(req_result) or bool(req_expl)
        for key in ("requires_figures", "expected_figures"):
            val = solve_spec.get(key)
            if isinstance(val, bool):
                return val
    combined = "\n".join(filter(None, [question, breakdown]))
    return bool(_FIGURE_KEYWORDS.search(combined))
