"""工作流引擎 - 按任务类型编排写作与润色流水线。"""
import json
import os
import re
import shutil
from pathlib import Path
from typing import AsyncGenerator

from app.artifacts.contracts import ProblemContract
from app.artifacts.answer_plan import AnswerPlanBuilder
from app.artifacts.registry import ArtifactRegistry
from app.artifacts.exporters import DocumentFinalizer, FinalizationValidationError
from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.diagnostics import (
    BudgetReport,
    WorkflowTrace,
)
from app.artifacts.artifact_evidence import (
    generated_file_path,
    generated_image_evidence_paths,
    has_generated_image_evidence,
    language_verified_generated_images,
)
from app.config.setting import settings
from app.core.agents.agent import Agent
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.figure_stage import FigureStage
from app.core.skills.visualization_planner import VisualizationPlannerSkill
from app.core.stages.figure_gate_stage import FigureGateStage
from app.core.stages.figure_recovery_stage import FigureRecoveryStage
from app.core.stages.finalizer_stage import FinalizerStage
from app.core.stages.quality_gate_stage import QualityGateStage
from app.core.stages.writer_stage import WriterStage
from app.core.llm.llm import LLM
from app.core.workflow_budget import CoderStageBudget, WorkflowBudget, normalize_workflow_mode, resolve_workflow_budget
from app.guidance.workflow import run_guidance_workflow
from app.core.prompts import POLISH_STAGE_SYSTEM_PROMPTS, WRITING_STAGE_SYSTEM_PROMPTS
from app.schemas.A2A import CoderToWriter
from app.schemas.enums import FormatOutPut, TaskStatus
from app.schemas.response import TaskProgress, TaskResult
from app.services.config_manager import config_manager
from app.services.task_service import task_manager
from app.tools.e2b_interpreter import E2BCodeInterpreter
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.common_utils import (
    build_paper_audit_report,
    build_paper_audit_manifest,
    build_problem_facts,
    build_result_registry,
    enforce_markdown_image_whitelist,
    finalize_markdown_export,
    get_current_files,
    load_json,
    normalize_math_markdown,
    save_json,
    validate_paper_audit_report,
)
from app.utils.figure_artifacts import (
    is_image_path,
    load_figure_manifest,
    normalize_artifact_path,
    save_figure_manifest,
)
from app.utils.log_util import logger


def _preview(text: str, limit: int = 320) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _message(agent: str, content: str, msg_type: str = "success", section: str = "") -> dict:
    payload = {"type": msg_type, "agent": agent, "content": content}
    if section:
        payload["section"] = section
    return payload


def _build_completed_task_result(
    task_id: str,
    task_type: str,
    work_dir: str,
    paper_path: str,
    docx_path: str,
    notebook_path: str,
    primary_artifact_type: str = "paper",
    audit_status: str = "",
    audit_summary: str = "",
    audit_blocks: list | None = None,
) -> dict:
    """Build a normalized completed TaskResult payload."""
    guidance_path = paper_path if primary_artifact_type == "guidance" else ""
    if primary_artifact_type == "guidance":
        docx_path = ""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="completed",
            task_type=task_type,
            primary_artifact_type=primary_artifact_type,
            guidance_path=guidance_path,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
            audit_status=audit_status,
            audit_summary=audit_summary,
            audit_blocks=audit_blocks,
        ).model_dump(),
    }


def _build_failed_task_result(
    task_id: str,
    task_type: str,
    work_dir: str,
    error_message: str,
    error_code: str = "",
    error_type: str = "",
    error_details: dict | None = None,
    paper_path: str = "",
    docx_path: str = "",
    notebook_path: str = "",
    primary_artifact_type: str = "paper",
    audit_status: str = "",
    audit_summary: str = "",
    audit_blocks: list | None = None,
) -> dict:
    """Build a normalized failed TaskResult payload."""
    guidance_path = paper_path if primary_artifact_type == "guidance" else ""
    if primary_artifact_type == "guidance":
        docx_path = ""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="failed",
            task_type=task_type,
            primary_artifact_type=primary_artifact_type,
            guidance_path=guidance_path,
            error_message=error_message,
            error_code=error_code,
            error_type=error_type,
            error_details=error_details,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
            audit_status=audit_status,
            audit_summary=audit_summary,
            audit_blocks=audit_blocks,
        ).model_dump(),
    }


def _classify_failure(exc: Exception) -> tuple[str, str, str, dict | None]:
    """Map internal exceptions to machine-readable failure semantics."""
    if isinstance(exc, FinalizationValidationError):
        return str(exc), exc.error_code, exc.error_type, exc.details
    return str(exc), "WORKFLOW_RUNTIME_ERROR", "runtime", None


def _unique_structured_result_files(*file_groups: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            filename
            for group in file_groups
            for filename in group
            if filename
        )
    )


def _generated_file_path(item: object) -> str:
    return generated_file_path(item)


def _is_image_path(path: str) -> bool:
    return is_image_path(path)


# ---------------------------------------------------------------------------
# Quality gate: verify that the workflow has produced enough verified results
# before allowing formal paper writing.  When the gate fails we emit a
# failure report instead of a paper that would contain no real data.
# ---------------------------------------------------------------------------

def _quality_gate_before_writing(
    result_registry: dict[str, object],
    expected_figures: bool,
    required_requests: list[dict[str, object]],
    satisfied_requests: list[dict[str, object]],
    missing_requests: list[dict[str, object]],
    workflow_mode: str,
    requires_result_figures: bool = False,
    requires_explanatory_figures: bool = False,
    result_figure_count: int = 0,
    explanatory_figure_count: int = 0,
    requires_figures_by_problem: bool = False,
    blocked_requests: list[dict[str, object]] | None = None,
) -> tuple[bool, str]:
    return FigureStage.quality_gate_before_writing(
        result_registry=result_registry,
        expected_figures=expected_figures,
        required_requests=required_requests,
        satisfied_requests=satisfied_requests,
        missing_requests=missing_requests,
        workflow_mode=workflow_mode,
        requires_result_figures=requires_result_figures,
        requires_explanatory_figures=requires_explanatory_figures,
        result_figure_count=result_figure_count,
        explanatory_figure_count=explanatory_figure_count,
        requires_figures_by_problem=requires_figures_by_problem,
        blocked_requests=blocked_requests,
    )


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


def _ensure_figure_requests_in_solve_spec(
    solve_spec: dict[str, object],
    chart_language: str,
) -> list[dict[str, object]]:
    return FigureStage.ensure_figure_requests_in_solve_spec(solve_spec, chart_language)


def _compat_figure_requests_from_solve_spec(
    solve_spec: dict[str, object],
    chart_language: str,
) -> list[dict[str, object]]:
    """Build legacy figure requests on a copy so solve_spec remains artifact-light."""
    if not isinstance(solve_spec, dict):
        return []
    compat_spec = dict(solve_spec)
    return FigureStage.ensure_figure_requests_in_solve_spec(compat_spec, chart_language)


def _required_figure_requests(solve_spec: dict[str, object]) -> list[dict[str, object]]:
    return FigureStage.required_figure_requests(solve_spec)


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
    return FigureStage.required_figure_requests(solve_spec or {})


def _figure_requests_for_subproblem(
    solve_spec: dict[str, object],
    subproblem_id: str,
) -> list[dict[str, object]]:
    return FigureStage.figure_requests_for_subproblem(solve_spec, subproblem_id)


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
    return FigureStage.figure_requests_for_subproblem(solve_spec or {}, sid)


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


def _figure_data_protocol_contract(requests: list[dict[str, object]]) -> str:
    return FigureStage.figure_data_protocol_contract(requests)


def _repair_eligible_missing_reason(reason: str) -> bool:
    return FigureStage.repair_eligible_missing_reason(reason)


def _repair_eligible_request(target_req: dict[str, object] | None, missing_reason: str) -> bool:
    return FigureStage.repair_eligible_request(target_req, missing_reason)


def _missing_reason_summary(missing_requests: list[dict[str, object]]) -> dict[str, int]:
    return FigureStage.missing_reason_summary(missing_requests)


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


def _apply_figure_plan_to_solve_spec(
    solve_spec: dict[str, object],
    figure_plan: dict[str, object],
) -> None:
    FigureStage.apply_figure_plan_to_solve_spec(solve_spec, figure_plan)


def _has_semantic_required_data_contract(role: str, required_data: list[str]) -> bool:
    """Check whether required_data is specific enough for semantic verification."""
    return FigureStage.has_semantic_required_data_contract(role, required_data)


def _deterministic_series_from_registry(
    result_registry: dict[str, object],
    required_data: list[str],
) -> tuple[list[float], list[float], list[str]]:
    return FigureStage.deterministic_series_from_registry(result_registry, required_data)


def _deterministic_series_from_data_files(
    work_dir: str,
    data_files: list[str],
) -> tuple[list[float], list[float], list[str]]:
    return FigureStage.deterministic_series_from_data_files(work_dir, data_files)


def _deterministic_named_columns_from_data_files(
    work_dir: str,
    data_files: list[str],
) -> tuple[dict[str, list[float]], list[str]]:
    return FigureStage.deterministic_named_columns_from_data_files(work_dir, data_files)


def _extract_named_series(
    columns: dict[str, list[float]],
    aliases: list[str],
) -> list[float]:
    return FigureStage.extract_named_series(columns, aliases)


def _generate_failure_report(
    question: str,
    result_registry: dict[str, object],
    stage_outputs: dict[str, object],
    gate_reason: str,
    work_dir: str,
) -> str:
    """Generate a structured failure report in Markdown when quality gate fails."""
    summary = result_registry.get("summary", {}) if isinstance(result_registry, dict) else {}
    verified_count = summary.get("verified_count", 0) if isinstance(summary, dict) else 0
    blocked_count = summary.get("blocked_count", 0) if isinstance(summary, dict) else 0
    blocked_results = result_registry.get("blocked_results", []) if isinstance(result_registry, dict) else []

    lines = [
        "# 求解失败报告 / Solver Failure Report",
        "",
        "## 质量门禁未通过 / Quality Gate Failed",
        "",
        f"**原因**: {gate_reason}",
        "",
        "## 求解统计 / Solver Statistics",
        "",
        f"| 指标 | 值 |",
        f"| --- | --- |",
        f"| verified_count | {verified_count} |",
        f"| blocked_count | {blocked_count} |",
        "",
    ]

    if blocked_results:
        lines.append("## 阻断项详情 / Blocked Results")
        lines.append("")
        for i, entry in enumerate(blocked_results[:10], 1):
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("id") or f"item_{i}"
                status = entry.get("status", "unknown")
                warnings = entry.get("warnings", [])
                warn_text = "; ".join(str(w) for w in warnings[:3]) if warnings else "none"
                lines.append(f"{i}. **{name}** — status: {status}, warnings: {warn_text}")
            else:
                lines.append(f"{i}. {entry}")
        lines.append("")

    lines.append("## 建议 / Recommendations")
    lines.append("")
    lines.append("1. 检查求解脚本是否正确加载数据并计算关键结果。")
    lines.append("2. 确认 key_results 的 status 字段被标记为 `verified` 而非 `blocked`。")
    lines.append("3. 如果存在 Excel 列名不匹配，请先打印 sheet/columns 再适配。")
    lines.append("4. 如果优化超时，降级为 coarse search + verified baseline。")
    lines.append("")

    return "\n".join(lines)


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


def _language_verified_generated_images(
    work_dir: str,
    structured_result_files: list[str],
    document_language: str,
    result_registry: dict[str, object] | None = None,
) -> list[str]:
    """Return image paths that pass the full FigureArtifact gate.

    ``result_registry``:
        When provided, ``linked_result_ids`` and ``source_data`` on each
        artifact are validated against the verified results in the registry.
        **All final paper-writing paths MUST pass a registry.**  Only
        per-file structural guards (e.g. ``coder_agent._ensure_structured_result_contract``)
        may pass ``None`` to skip semantic binding — those guards run before
        the registry exists and are not the final export gate.
    """
    return language_verified_generated_images(
        work_dir,
        structured_result_files,
        document_language,
        result_registry,
    )


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


def _has_generated_image_evidence(
    work_dir: str,
    structured_result_files: list[str],
    created_images: list[str] | None = None,
) -> bool:
    return has_generated_image_evidence(work_dir, structured_result_files, created_images)


def _generated_image_evidence_paths(
    work_dir: str,
    structured_result_files: list[str],
    created_images: list[str] | None = None,
) -> list[str]:
    return generated_image_evidence_paths(work_dir, structured_result_files, created_images)


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


def _resolve_question(task: dict) -> str:
    question = (task.get("question") or "").strip()
    source_question = (task.get("source_question") or "").strip()
    source_text = (task.get("source_question_text") or "").strip()

    if source_question and question and source_question != question:
        return source_question
    if source_question:
        return source_question

    if source_text and question and source_text != question:
        return f"{question}\n\n# 附件原题文本\n{source_text}"
    if source_text:
        return source_text
    return question


def _detect_document_language(*texts: str) -> str:
    combined = "\n".join(text for text in texts if text)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", combined))
    latin_words = len(re.findall(r"\b[A-Za-z][A-Za-z0-9'-]*\b", combined))

    if latin_words >= 80 and latin_words >= cjk_count * 2:
        return "English"
    if cjk_count >= 30 and cjk_count > latin_words:
        return "Simplified Chinese"
    return "English" if latin_words > cjk_count else "Simplified Chinese"


def _chart_language_policy(document_language: str) -> str:
    if document_language == "English":
        return (
            "## Chart language policy\n"
            "- The document/problem language is English. All visible chart text must be English: titles, axis labels, legends, annotations, colorbar labels, and in-figure captions.\n"
            "- Do not use Chinese labels in generated figures for this task.\n"
            "- Prefer ASCII English labels to avoid missing-font boxes in PNG output.\n"
        )
    return (
        "## Chart language policy\n"
        "- The document/problem language is Simplified Chinese. All visible chart text must use Simplified Chinese: titles, axis labels, legends, annotations, colorbar labels, tick-category labels, and in-figure captions.\n"
        "- Translate generic labels such as Time, Distance, Speed, Heading, Shading duration, Solution, Parameter, Value, Unit, UAV, and Missile into Chinese. Keep units such as m, s, m/s, rad, and kg unchanged.\n"
        "- Do not leave English-only chart labels in figures for Chinese tasks.\n"
        "- Keep labels short and rely on the environment's preset matplotlib font configuration.\n"
    )


def _strict_chart_artifact_contract(document_language: str) -> str:
    expected = "English" if document_language == "English" else "Simplified Chinese"
    if document_language == "English":
        audit_example = '["Wavelength (nm)", "Reflectance (%)", "Fitted curve"]'
        example_filename = "problem1_trajectory.png"
    else:
        audit_example = '["波长/nm", "反射率/%", "拟合曲线"]'
        example_filename = "problem1_trajectory.png"
    return (
        "## Strict chart artifact contract\n"
        "- Use save_paper_figure(fig, filename, visible_text_audit=[...], linked_result_ids=[...], source_data=[...]) for every final paper figure.\n"
        "- Do not use plt.savefig/fig.savefig for deliverable images.\n"
        "- You MUST use the exact dict returned by save_paper_figure() as the generated_files entry. "
        "Do NOT hand-write or fabricate the artifact object — the pipeline verifies created_by and helper_version fields "
        "that only save_paper_figure() can produce.\n"
        f"- Example call: save_paper_figure(fig, '{example_filename}', visible_text_audit={audit_example}, linked_result_ids=['result_id_1'], source_data=['result_registry.json'])\n"
        "- Expected return value shape: "
        "{\"path\":\"output/figure.png\",\"kind\":\"figure\",\"paper_ready\":true,"
        f"\"visible_text_language\":\"{expected}\",\"chart_language_verified\":true,"
        f"\"visible_text_audit\":{audit_example},"
        "\"created_by\":\"save_paper_figure\",\"helper_version\":3,"
        "\"is_placeholder\":false,\"figure_role\":\"result_summary\","
        "\"linked_result_ids\":[\"result_id_1\"],\"source_data\":[\"result_registry.json\"]}.\n"
        f"- visible_text_audit must contain the ACTUAL visible text strings rendered on the figure in {expected}. "
        "Do NOT use generic field names like 'title', 'axis labels', 'legend'.\n"
        "- If any visible chart text does not match the Chart language policy, set paper_ready=false and save the file under debug_artifacts/ or redraw it before registration.\n"
        "- The writer will only receive images that pass this metadata gate; unverified images are intentionally withheld from the paper.\n"
        "- For required paper figures, you must also provide semantic fields in the generated_files artifact object: "
        "figure_request_id, subproblem_id, semantic_role, depicts, data_bindings, semantic_verified=true.\n"
        "- Diagnostic charts (including verified_result_summary) must use semantic_verified=false and paper_section='diagnostics'.\n"
    )


def _chart_language_context(document_language: str) -> dict[str, object]:
    if document_language == "English":
        return {
            "document_language": "English",
            "chart_language": "English",
            "required_visible_text": "English",
            "final_image_contract": {
                "paper_ready": True,
                "visible_text_language": "English",
                "chart_language_verified": True,
                "is_placeholder": False,
                "figure_role": "result_summary",
                "linked_result_ids": [],
                "source_data": [],
            },
            "rules": [
                "All visible chart text must be English.",
                "Do not cite or register Chinese-labeled figures as paper_ready.",
                "Use save_paper_figure for final figures.",
                "Every figure must have linked_result_ids referencing verified results.",
                "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
            ],
        }
    return {
        "document_language": "Simplified Chinese",
        "chart_language": "Simplified Chinese",
        "required_visible_text": "Simplified Chinese",
        "font_policy": "Use container Chinese fonts configured by LocalCodeInterpreter.",
        "final_image_contract": {
            "paper_ready": True,
            "visible_text_language": "Simplified Chinese",
            "chart_language_verified": True,
            "is_placeholder": False,
            "figure_role": "result_summary",
            "linked_result_ids": [],
            "source_data": [],
        },
        "rules": [
            "All visible chart text must be Simplified Chinese.",
            "English is allowed only for units, formulas, and established symbols.",
            "Use save_paper_figure for final figures.",
            "Every figure must have linked_result_ids referencing verified results.",
            "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
        ],
    }


def _image_manifest_text(image_manifest: list[dict] | None) -> str:
    if not image_manifest:
        return "未提供图片资源"

    rows = ["| 图片路径 | 是否被 Markdown 引用 | 大小(KB) |", "| --- | --- | ---: |"]
    for item in image_manifest:
        rows.append(
            f"| {item.get('path', '')} | {'是' if item.get('referenced') else '否'} | {item.get('size_kb', 0)} |"
        )
    return "\n".join(rows)


def _progress(
    task_id: str,
    stage: str,
    progress: float,
    message: str,
    status: str = "running",
    current_subtask: str = "",
) -> dict:
    return {
        "type": "progress",
        "data": TaskProgress(
            task_id=task_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            current_subtask=current_subtask,
        ).model_dump(),
    }


def _json_for_prompt(payload: object, limit: int = 12000) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 16] + "\n...\n}"


def _compact_result_registry_for_prompt(result_registry: dict | None, max_verified: int = 60, max_blocked: int = 8) -> dict:
    """Keep high-signal computed results visible inside prompt limits."""
    if not isinstance(result_registry, dict):
        return {"verified_results": [], "blocked_results": [], "summary": {}}

    verified = list(result_registry.get("verified_results", []) or [])
    blocked = list(result_registry.get("blocked_results", []) or [])
    source_files = result_registry.get("source_files", []) or []
    summary = result_registry.get("summary", {}) or {}

    def result_priority(entry: object) -> tuple[int, str]:
        if not isinstance(entry, dict):
            return (9, "")
        source = str(entry.get("source", ""))
        name = str(entry.get("name", ""))
        value = str(entry.get("value", ""))
        if source == "artifact_salvage_summary":
            return (0, name)
        if "RMSE" in name or "R²" in name or "R2" in name or "误差" in name or "比例" in name:
            return (1, name)
        if entry.get("source_data") and value.strip():
            return (2, name)
        if value.strip():
            return (3, name)
        return (4, name)

    compact_verified = sorted(verified, key=result_priority)[:max_verified]
    compact_blocked = blocked[:max_blocked]
    omitted_verified = max(0, len(verified) - len(compact_verified))
    omitted_blocked = max(0, len(blocked) - len(compact_blocked))

    return {
        "summary": {
            **summary,
            "prompt_compacted": True,
            "verified_in_prompt": len(compact_verified),
            "blocked_in_prompt": len(compact_blocked),
            "omitted_verified": omitted_verified,
            "omitted_blocked": omitted_blocked,
        },
        "source_files": source_files,
        "verified_results": compact_verified,
        "blocked_results": compact_blocked,
        "writing_rule": (
            "若 verified_results 与 blocked_results 同时存在，只能对 blocked 条目降级；"
            "不得把已在 verified_results 中登记的结果写成未计算或未获取。"
        ),
    }


def _merge_audit_reports(primary: dict | None, fallback: dict) -> dict:
    if not validate_paper_audit_report(primary):
        return fallback

    blocks = []
    seen: set[tuple[str, str, str]] = set()
    for source in [primary.get("blocks", []), fallback.get("blocks", [])]:
        for block in source:
            marker = (str(block.get("type", "")), str(block.get("location", "")), str(block.get("fix", "")))
            if marker in seen:
                continue
            seen.add(marker)
            blocks.append(block)

    return {
        "status": "BLOCK" if primary.get("status") == "BLOCK" or fallback.get("status") == "BLOCK" or blocks else "PASS",
        "blocks": blocks,
        "programmatic_checks": fallback.get("programmatic_checks", {}),
        "llm_summary": primary.get("llm_summary") or fallback.get("llm_summary", ""),
    }


def _build_models(budget: WorkflowBudget) -> dict[str, LLM]:
    return {
        "breakdown": LLM(model=config_manager.get_effective_model("COORDINATOR_MODEL"), temperature=0.2, max_tokens=8192, request_timeout=budget.llm_timeout),
        "modeling": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.35, max_tokens=12288, request_timeout=budget.llm_timeout),
        "review": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.2, max_tokens=8192, request_timeout=budget.llm_timeout),
        "coder": LLM(model=config_manager.get_effective_model("CODER_MODEL"), temperature=0.2, max_tokens=12288, request_timeout=budget.llm_timeout),
        "analysis": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.35, max_tokens=12288, request_timeout=budget.llm_timeout),
        "writer": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.45, max_tokens=16384, request_timeout=budget.writer.request_timeout),
    }


STRICT_WRITER_SECTION_SPECS = [
    ("摘要与关键词", "输出摘要与关键词，突出问题、方法、核心结果与结论。"),
    ("问题重述与分析", "输出问题重述、问题分析与任务拆解，不要提前写求解细节。"),
    ("模型假设与符号说明", "输出模型假设、符号说明、变量定义与必要前提。"),
    ("模型建立", "输出模型建立、公式推导与方法设计。"),
    ("求解结果与分析", "输出算法求解、关键结果、图表解读、误差或灵敏度分析。所有具体数值都必须遵守 verified_results 约束。"),
    ("结论与参考文献", "输出结论、模型评价与参考文献。若没有可靠文献，可省略参考文献章节但不能编造。"),
]


async def _run_writer_stage(
    writer: WriterAgent,
    prompt: str,
    available_images: list[str],
    sub_title: str,
    budget: WorkflowBudget,
) -> str:
    if not budget.writer.sectioned:
        try:
            writer_result = await writer.run(
                prompt=prompt,
                available_images=available_images,
                sub_title=sub_title,
            )
            return writer_result.response_content
        except RuntimeError as exc:
            error_text = str(exc)
            if "超时" not in error_text and "timeout" not in error_text.lower():
                raise
            logger.warning("WriterAgent 整篇生成超时，切换为分章节写作兜底: %s", error_text)

    section_outputs: list[str] = []
    for index, (section_title, section_instruction) in enumerate(STRICT_WRITER_SECTION_SPECS, start=1):
        section_writer = WriterAgent(
            task_id=writer.task_id,
            model=writer.model,
            max_chat_turns=budget.writer.max_chat_turns,
            comp_template=writer.comp_template,
            format_output=writer.format_out_put,
            scholar=writer.scholar,
            max_memory=writer.max_memory,
        )
        section_writer.system_prompt = writer.system_prompt
        completed_sections = "\n".join(
            f"- 第{done_index}节：{title}"
            for done_index, (title, _) in enumerate(STRICT_WRITER_SECTION_SPECS[: index - 1], start=1)
        ) or "无"
        section_prompt = (
            f"{prompt}\n\n"
            f"## 当前章节任务\n"
            f"你当前只负责第{index}节《{section_title}》。\n"
            f"章节要求：{section_instruction}\n"
            f"已完成章节：\n{completed_sections}\n\n"
            "只输出本章节 Markdown，不要重复其他章节，不要输出全文，不要使用 ```markdown 代码块包裹。"
            "除《结论与参考文献》章节外，不要调用 search_papers。"
        )
        section_result = await section_writer.run(
            prompt=section_prompt,
            available_images=available_images,
            sub_title=f"{sub_title}-{section_title}",
        )
        section_outputs.append(section_result.response_content.strip())

    return "\n\n".join(part for part in section_outputs if part)


def _resolve_workflow_mode(task: dict) -> str:
    return normalize_workflow_mode(task.get("workflow_mode") or settings.WORKFLOW_MODE)


def _writer_mode_policy(workflow_mode: str) -> str:
    mode = normalize_workflow_mode(workflow_mode)
    if mode == "fast":
        return (
            "快速模式写作策略：优先快速产出可用模型方案和核心计算流程，正文保持短稿；"
            "只写必要模型、关键公式、主要结果和风险提示，避免完整竞赛论文式展开。"
        )
    if mode == "strict":
        return (
            "严格模式写作策略：严格体现在数值、单位、公式口径、复核和证据链上，而不是长篇大论。"
            "正文必须克制，禁止重复推导、泛泛背景、空洞优缺点和无证据扩写；"
            "每个关键数值表必须能回指 result_registry 的 verified 证据。"
            "若结果不可靠，应明确阻断或降级，不能用冗长文字掩盖。"
        )
    return (
        "标准模式写作策略：在完整性与篇幅之间取中庸，保留必要建模说明、核心推导、关键表格、"
        "结果分析和局限性，不做无关扩写。"
    )


def _review_has_blocking_issues(review_text: str) -> bool:
    keywords = ["无法求解", "定义缺失", "公式错误", "符号不一致", "逻辑跳跃", "证据不足"]
    text = str(review_text or "")
    return any(keyword in text for keyword in keywords)


def _build_verify_plan(result_registry: dict[str, object]) -> dict[str, object]:
    def is_blank_result(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) == 0
        return False

    items: list[dict[str, object]] = []
    for entry in (result_registry.get("verified_results", []) or []) + (result_registry.get("blocked_results", []) or []):
        if not isinstance(entry, dict):
            continue
        value = entry.get("value", "")
        paper_value = entry.get("paper_value", "")
        computed_value = entry.get("computed_value", "")
        if is_blank_result(value) and is_blank_result(paper_value) and is_blank_result(computed_value):
            continue
        items.append(
            {
                "id": entry.get("id", ""),
                "name": entry.get("name", ""),
                "value": value,
                "paper_value": paper_value,
                "computed_value": computed_value,
                "unit": entry.get("unit", ""),
                "formula": entry.get("formula", ""),
                "inputs": entry.get("inputs", {}),
                "source_data": entry.get("source_data", []),
                "source_file": entry.get("source_file", ""),
                "status": entry.get("status", "unverified"),
                "tolerance": 1e-6,
            }
        )
    return {
        "items": items,
        "summary": {
            "item_count": len(items),
            "verified_seed_count": len(result_registry.get("verified_results", []) or []),
            "blocked_seed_count": len(result_registry.get("blocked_results", []) or []),
        },
    }


def _verify_plan_requires_agent(verify_plan: dict[str, object]) -> bool:
    items = verify_plan.get("items", []) or []
    if not isinstance(items, list) or not items:
        return False
    return any(str(item.get("status", "")).lower() != "blocked" for item in items if isinstance(item, dict))


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


def _extract_json_object(text: str) -> dict[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    candidates = [raw]
    fence_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.S)
    if fence_match:
        candidates.insert(0, fence_match.group(1))

    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidates.append(raw[brace_start:brace_end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _fallback_solve_spec(
    question: str,
    breakdown: str,
    modeling: str,
    review: str,
    problem_facts: dict[str, object],
) -> dict[str, object]:
    return {
        "subproblems": [
            {
                "id": "main",
                "objective": "基于题目拆解、建模方案和题设事实完成可复现求解",
                "input_files": [],
                "input_columns": [],
                "method": "derive_from_modeling_plan",
                "steps": [
                    "读取 data 目录与题设事实",
                    "按建模方案选择可执行算法",
                    "输出可复核关键结果到结构化结果文件",
                ],
                "expected_outputs": [
                    {
                        "id": "main_key_results",
                        "type": "key_results",
                    }
                ],
                "validation": [
                    "关键结果必须写入结构化结果文件",
                    "题设参数不得被擅自修改",
                ],
            }
        ],
        "source": "fallback",
        "question_excerpt": _preview(question, 500),
        "breakdown_excerpt": _preview(breakdown, 1200),
        "modeling_excerpt": _preview(modeling, 1200),
        "review_excerpt": _preview(review, 1000),
        "locked_parameters": problem_facts.get("locked_parameters", {}),
    }


def _solve_spec_subproblems(solve_spec: dict[str, object]) -> list[dict[str, object]]:
    raw_subproblems = solve_spec.get("subproblems", []) if isinstance(solve_spec, dict) else []
    if not isinstance(raw_subproblems, list):
        return []
    subproblems: list[dict[str, object]] = []
    for index, item in enumerate(raw_subproblems, start=1):
        if isinstance(item, dict):
            normalized = dict(item)
        else:
            normalized = {"objective": str(item)}
        normalized.setdefault("id", f"p{index}")
        subproblems.append(normalized)
    return subproblems


def _subproblem_label(index: int, subproblem: dict[str, object]) -> str:
    raw_label = str(
        subproblem.get("id")
        or subproblem.get("title")
        or subproblem.get("name")
        or f"p{index}"
    ).strip()
    label = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", raw_label).strip("_")
    return label or f"p{index}"


def _should_split_solver(workflow_mode: str, solve_spec: dict[str, object]) -> bool:
    return normalize_workflow_mode(workflow_mode) != "fast" and len(_solve_spec_subproblems(solve_spec)) > 1


def _subproblem_figure_mode(
    subproblem: dict[str, object],
    task_expected_figures: bool,
) -> str:
    """Determine figure_mode for a specific subproblem.

    Even when the overall task expects figures, a subproblem that is purely
    numerical (no figure keywords in its objective/steps/expected_outputs)
    gets ``"none"`` to avoid polluting it with figure protocol.
    """
    return FigureStage.subproblem_figure_mode(subproblem, task_expected_figures)


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


def _subproblem_complexity_score(subproblem: dict[str, object]) -> int:
    try:
        text = json.dumps(subproblem, ensure_ascii=False).lower()
    except Exception:
        text = str(subproblem).lower()

    score = 1
    hint = str(subproblem.get("complexity_hint") or subproblem.get("complexity") or "").strip().lower()
    priority = str(subproblem.get("priority") or "").strip().lower()
    if hint in {"simple", "low", "低", "简单"}:
        score = 1
    elif hint in {"medium", "中", "中等", "标准"}:
        score = 3
    elif hint in {"complex", "high", "高", "复杂"}:
        score = 6
    if priority == "high" or priority == "高":
        score += 1
    input_files = subproblem.get("input_files", [])
    steps = subproblem.get("steps", [])
    expected_outputs = subproblem.get("expected_outputs", [])
    if isinstance(input_files, list):
        score += min(2, len(input_files) // 2)
    if isinstance(steps, list):
        score += min(2, len(steps) // 4)
    if isinstance(expected_outputs, list):
        score += min(1, len(expected_outputs) // 3)

    complex_markers = [
        "machine learning",
        "random forest",
        "svr",
        "xgboost",
        "lstm",
        "neural",
        "cross validation",
        "交叉验证",
        "网格搜索",
        "优化",
        "非线性",
        "mice",
        "插补",
        "异常检测",
        "预测",
        "预警",
        "变量组合",
        "敏感性",
        "monte carlo",
    ]
    medium_markers = ["回归", "拟合", "分类", "聚类", "变点", "滤波", "平滑", "参数估计", "误差分析"]
    score += sum(1 for marker in complex_markers if marker in text)
    score += min(2, sum(1 for marker in medium_markers if marker in text))
    return max(1, min(score, 8))


def _subproblem_budget_level(score: int) -> str:
    if score >= 6:
        return "complex"
    if score >= 3:
        return "medium"
    return "simple"


def _solver_budget_for_subproblem(budget: WorkflowBudget, subproblem: dict[str, object] | None = None) -> CoderStageBudget:
    score = _subproblem_complexity_score(subproblem or {})
    if budget.mode == "strict":
        tool_calls = 18 if score <= 2 else 26 if score <= 5 else min(36, budget.solver.max_total_tool_calls)
        wall_seconds = 360 if score <= 2 else 540 if score <= 5 else budget.solver.max_wall_seconds
        return CoderStageBudget(
            max_chat_turns=budget.solver.max_chat_turns,
            max_retries=budget.solver.max_retries,
            max_total_tool_calls=min(max(tool_calls, budget.solver.max_total_tool_calls // 2), budget.solver.max_total_tool_calls),
            max_wall_seconds=min(max(wall_seconds, budget.solver.max_wall_seconds // 2), budget.solver.max_wall_seconds),
        )
    tool_calls = 16 if score <= 2 else 22 if score <= 5 else min(32, max(28, budget.solver.max_total_tool_calls + 4))
    wall_seconds = 360 if score <= 2 else 540 if score <= 5 else min(900, max(720, budget.solver.max_wall_seconds))
    return CoderStageBudget(
        max_chat_turns=budget.solver.max_chat_turns,
        max_retries=budget.solver.max_retries,
        max_total_tool_calls=tool_calls,
        max_wall_seconds=wall_seconds,
    )


def _build_code_interpreter(work_dir: str, chart_language: str = "English"):
    if settings.CODE_INTERPRETER == "e2b" and settings.E2B_API_KEY:
        return E2BCodeInterpreter(api_key=settings.E2B_API_KEY, work_dir=work_dir)
    return LocalCodeInterpreter(work_dir=work_dir, chart_language=chart_language)


async def run_workflow(task_id: str, question: str) -> AsyncGenerator[dict, None]:
    task = task_manager.get_task(task_id)
    if not task:
        yield {"error": "任务不存在"}
        return

    task_type = task.get("task_type", "writing")
    if task_type == "polish":
        from app.polish import workflow as polish_workflow

        async for payload in polish_workflow.run_polish_workflow(task_id, task):
            yield payload
        return

    async for payload in run_guidance_workflow(task_id, task):
        yield payload


async def run_revision_workflow(
    revision_task_id: str,
    revision_task: dict,
) -> AsyncGenerator[dict, None]:
    """历史论文修订统一走润色流水线。"""
    revision_task["task_type"] = "polish"
    if not revision_task.get("paper_content") and revision_task.get("parent_task_id"):
        revision_task["paper_content"] = task_manager.load_paper(revision_task.get("parent_task_id", "")) or ""

    from app.polish import workflow as polish_workflow

    async for payload in polish_workflow.run_polish_workflow(revision_task_id, revision_task):
        yield payload



