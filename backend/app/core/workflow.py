"""工作流引擎 - 按任务类型编排写作与润色流水线。"""
import json
import os
import re
import shutil
from pathlib import Path
from typing import AsyncGenerator

from app.artifacts.contracts import ProblemContract
from app.artifacts.registry import ArtifactRegistry
from app.artifacts.exporters import DocumentFinalizer, FinalizationValidationError
from app.artifacts.figure_plan import FigurePlanBuilder
from app.artifacts.diagnostics import (
    FigureGateReport,
    QualityGateReport,
    BudgetReport,
    WorkflowTrace,
)
from app.config.setting import settings
from app.core.agents.agent import Agent
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.figure_stage import FigureStage
from app.core.skills.renderer import RendererSkill
from app.core.skills.visualization_planner import VisualizationPlannerSkill
from app.core.stages.figure_gate_stage import FigureGateStage
from app.core.stages.writer_stage import WriterStage
from app.core.llm.llm import LLM
from app.core.workflow_budget import CoderStageBudget, WorkflowBudget, normalize_workflow_mode, resolve_workflow_budget
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
    FIGURE_MANIFEST,
    figure_artifact_path,
    is_image_path,
    is_verified_paper_figure,
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
) -> dict:
    """Build a normalized completed TaskResult payload."""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="completed",
            task_type=task_type,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
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
) -> dict:
    """Build a normalized failed TaskResult payload."""
    return {
        "type": "result",
        "data": TaskResult(
            task_id=task_id,
            status="failed",
            task_type=task_type,
            error_message=error_message,
            error_code=error_code,
            error_type=error_type,
            error_details=error_details,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
            work_dir=work_dir,
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
    return figure_artifact_path(item)


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


def _required_figure_requests(solve_spec: dict[str, object]) -> list[dict[str, object]]:
    return FigureStage.required_figure_requests(solve_spec)


def _figure_requests_for_subproblem(
    solve_spec: dict[str, object],
    subproblem_id: str,
) -> list[dict[str, object]]:
    return FigureStage.figure_requests_for_subproblem(solve_spec, subproblem_id)


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


def _render_required_requests_deterministically(
    work_dir: str,
    result_registry: dict[str, object],
    required_requests: list[dict[str, object]],
    chart_language: str,
) -> dict[str, object]:
    return RendererSkill.render_required_requests(
        work_dir=work_dir,
        result_registry=result_registry,
        required_requests=required_requests,
        chart_language=chart_language,
    ).to_dict()


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
    allowed: list[str] = []
    seen: set[str] = set()

    # Build the set of verified result IDs for semantic binding validation.
    verified_ids: set[str] | None = None
    if result_registry is not None:
        verified_ids = set()
        for entry in result_registry.get("verified_results", []) or []:
            if isinstance(entry, dict) and entry.get("id"):
                verified_ids.add(str(entry["id"]))

    def maybe_add(item: object, source: str) -> None:
        path = _generated_file_path(item)
        if not path or path in seen:
            return
        if not is_verified_paper_figure(item, document_language, work_dir, verified_ids):
            if path and _is_image_path(path):
                logger.warning("Excluded image from paper FigureArtifact gate ({}): {}", source, item)
            return
        allowed.append(path)
        seen.add(path)

    for manifest_item in load_figure_manifest(work_dir):
        maybe_add(manifest_item, FIGURE_MANIFEST)

    root = Path(work_dir)
    for filename in structured_result_files:
        payload = load_json(str(root / filename))
        if not isinstance(payload, dict):
            continue
        generated_files = payload.get("generated_files", [])
        if not isinstance(generated_files, list):
            continue
        for item in generated_files:
            maybe_add(item, filename)
    return allowed


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
    return bool(_generated_image_evidence_paths(work_dir, structured_result_files, created_images))


def _generated_image_evidence_paths(
    work_dir: str,
    structured_result_files: list[str],
    created_images: list[str] | None = None,
) -> list[str]:
    evidence: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        normalized = normalize_artifact_path(path)
        if not normalized or normalized in seen or not _is_image_path(normalized):
            return
        evidence.append(normalized)
        seen.add(normalized)

    for path in created_images or []:
        add(path)
    if load_figure_manifest(work_dir):
        for item in load_figure_manifest(work_dir):
            add(_generated_file_path(item))
    root = Path(work_dir)
    for filename in structured_result_files:
        payload = load_json(str(root / filename))
        if not isinstance(payload, dict):
            continue
        generated_files = payload.get("generated_files", [])
        if isinstance(generated_files, list):
            for item in generated_files:
                add(_generated_file_path(item))
        artifact_evidence = payload.get("artifact_evidence", [])
        if isinstance(artifact_evidence, list):
            for item in artifact_evidence:
                add(_generated_file_path(item))

    debug_dir = root / "debug_artifacts"
    if debug_dir.exists():
        for path in sorted(debug_dir.rglob("*")):
            if path.is_file() and _is_image_path(path.name):
                add(path.relative_to(root).as_posix())

    return evidence


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


def _needs_keyword(text: str, patterns: list[str]) -> bool:
    haystack = (text or "").lower()
    return any(pattern.lower() in haystack for pattern in patterns)


def _detect_repair_routes(*reports: str) -> list[str]:
    combined = "\n".join(report for report in reports if report)
    routes: set[str] = set()

    if _needs_keyword(
        combined,
        [
            "题设参数",
            "参数被擅自修改",
            "符号不一致",
            "单位不一致",
            "公式编号",
            "模型定义",
            "变量定义",
        ],
    ):
        routes.add("modeling")

    if _needs_keyword(
        combined,
        [
            "无法复现",
            "复算",
            "数值不一致",
            "表格值",
            "关键数值",
            "不可验证计算",
            "阻断项",
        ],
    ):
        routes.add("solver")

    if _needs_keyword(
        combined,
        [
            "参考文献",
            "占位",
            "虚构",
            "图文不一致",
            "降级措辞",
            "写作",
            "writer",
        ],
    ):
        routes.add("writer")

    if "modeling" in routes:
        routes.add("solver")
    if "solver" in routes:
        routes.add("writer")

    if not routes and re.search(r"\bBLOCK\b", combined, re.I):
        routes.add("writer")

    return [route for route in ["modeling", "solver", "writer"] if route in routes]


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


def _routes_from_audit_report(report: dict | None) -> list[str]:
    if not isinstance(report, dict):
        return []
    routes = []
    for block in report.get("blocks", []):
        route = str(block.get("route", "")).strip()
        if route in {"modeling", "solver", "writer"} and route not in routes:
            routes.append(route)
    if "modeling" in routes and "solver" not in routes:
        routes.append("solver")
    if "solver" in routes and "writer" not in routes:
        routes.append("writer")
    return routes


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
    """Regenerate figure_plan from verified results and mirror requests to solve_spec."""
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
    solve_spec["figure_requests"] = figure_requests
    _apply_figure_plan_to_solve_spec(solve_spec, figure_plan)
    save_json(solve_spec, solve_spec_path)
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


def _resolve_polish_contract(
    task: dict[str, object],
    workflow_mode: str,
    question: str,
    paper_content: str,
) -> ProblemContract:
    """Resolve polish contract with parent-contract priority.

    Priority:
    1) Inherit parent task contract (revision/polish subtask)
    2) Fall back to question + paper_content combined text
    """
    contract = None
    parent_task_id = str(task.get("parent_task_id") or "").strip()
    if parent_task_id:
        parent_task = task_manager.get_task(parent_task_id) or {}
        parent_work_dir = str(parent_task.get("work_dir") or "").strip()
        if parent_work_dir:
            contract = ProblemContract.load(parent_work_dir)

    if contract is None:
        combined_source = "\n\n".join(
            [part.strip() for part in (question, paper_content) if str(part).strip()]
        )
        contract = ProblemContract.from_question(combined_source, workflow_mode=workflow_mode)

    if contract.workflow_mode != workflow_mode:
        contract = ProblemContract(
            document_language=contract.document_language,
            chart_language=contract.chart_language,
            allowed_visible_text_language=contract.allowed_visible_text_language,
            font_profile=contract.font_profile,
            paper_requires_figures=contract.paper_requires_figures,
            paper_requires_tables=contract.paper_requires_tables,
            workflow_mode=workflow_mode,
            chart_language_aliases=contract.chart_language_aliases,
            allowed_latin_tokens=contract.allowed_latin_tokens,
        )

    return contract


async def _run_text_agent(
    task_id: str,
    model: LLM,
    system_prompt: str,
    user_prompt: str,
    sub_title: str,
) -> str:
    agent = Agent(task_id=task_id, model=model, max_chat_turns=10, max_memory=12)
    response = await agent.run(prompt=user_prompt, system_prompt=system_prompt, sub_title=sub_title)
    return response.strip()


async def _writing_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    question = _resolve_question(task)
    work_dir = task["work_dir"]
    workflow_mode = _resolve_workflow_mode(task)
    budget = resolve_workflow_budget(workflow_mode)
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    if not question.strip():
        raise ValueError("写作任务缺少原始题目内容，请输入题目或上传 docx/pdf 原题文件")

    # ── Create ProblemContract (immutable language + requirements) ──────
    # Initial contract with best-effort figure detection; will be upgraded
    # after solve_spec is generated.
    contract = ProblemContract.from_question(question, workflow_mode=workflow_mode)
    contract.save(work_dir)
    logger.info("ProblemContract created: lang={}, figures={}, mode={}",
                contract.chart_language, contract.paper_requires_figures, contract.workflow_mode)

    # Derive all language fields from contract (single source of truth)
    chart_language = contract.chart_language
    chart_language_policy = _chart_language_policy(chart_language)
    chart_language_context = contract.chart_language_context

    models = _build_models(budget)
    code_interpreter = _build_code_interpreter(work_dir, chart_language)
    scholar = OpenAlexScholar()
    trace = WorkflowTrace()
    budget_report = BudgetReport()

    data_context = get_current_files(work_dir, "data")
    stage_outputs: dict[str, object] = {}
    chart_images: list[str] = []
    solver_images: list[str] = []
    problem_facts: dict[str, object] = {}
    result_registry: dict[str, object] = {"verified_results": [], "blocked_results": [], "summary": {"verified_count": 0, "blocked_count": 0}}
    problem_facts_path = os.path.join(work_dir, "problem_facts.json")
    result_registry_path = os.path.join(work_dir, "result_registry.json")
    verify_plan_path = os.path.join(work_dir, "verify_plan.json")
    solve_spec_path = os.path.join(work_dir, "solve_spec.json")

    try:
        yield _progress(task_id, "breakdown", 0.06, "题目拆解 Agent 正在分析赛题", current_subtask="题目拆解")
        import time as _time
        _stage_start = _time.monotonic()
        breakdown_prompt = (
            f"# Document and chart language lock\n{_json_for_prompt(chart_language_context, 4000)}\n\n"
            "The task breakdown must explicitly carry this language lock forward. All later modeling, solver code, chart code, generated_files metadata, and writing must follow it.\n\n"
            f"# 用户题目\n{question}\n\n"
            f"# 当前数据文件\n{data_context}\n\n"
            "请严格按要求完成结构化题目拆解，为后续建模和求解提供输入。"
        )
        stage_outputs["breakdown"] = await _run_text_agent(
            task_id,
            models["breakdown"],
            WRITING_STAGE_SYSTEM_PROMPTS["breakdown"],
            breakdown_prompt,
            "题目拆解",
        )
        problem_facts = build_problem_facts(question, str(stage_outputs["breakdown"]))
        problem_facts["document_language"] = chart_language
        problem_facts["chart_language"] = chart_language
        problem_facts["chart_language_context"] = chart_language_context
        problem_facts["chart_language_policy"] = chart_language_policy
        save_json(problem_facts, problem_facts_path)
        stage_outputs["problem_facts"] = problem_facts
        stage_outputs["problem_facts_file"] = os.path.basename(problem_facts_path)
        stage_outputs["workflow_mode"] = workflow_mode
        stage_outputs["chart_language"] = chart_language
        trace.add_stage("breakdown", "completed", "题目拆解完成", _time.monotonic() - _stage_start)
        yield _message("breakdown", _preview(str(stage_outputs["breakdown"])))
        if workflow_mode == "fast":
            stage_outputs["modeling"] = "Fast 模式跳过独立建模阶段，求解直接基于题目拆解与题设事实执行。"
            stage_outputs["review"] = "Fast 模式跳过独立模型审查阶段。"
            solve_spec = _fallback_solve_spec(
                question,
                str(stage_outputs["breakdown"]),
                str(stage_outputs["modeling"]),
                str(stage_outputs["review"]),
                problem_facts,
            )
        else:
            yield _progress(task_id, "modeling", 0.18, "假设与建模 Agent 正在建立模型", current_subtask="模型建立")
            modeling_prompt = (
                f"# 题目\n{question}\n\n"
                f"# 题目拆解结果\n{stage_outputs['breakdown']}\n\n"
                f"# 题设事实锁定文件\n{os.path.basename(problem_facts_path)}\n\n"
                f"# 题设事实锁定内容\n{_json_for_prompt(problem_facts, 8000)}\n\n"
                "请输出可直接审查的建模方案。"
            )
            stage_outputs["modeling"] = await _run_text_agent(
                task_id,
                models["modeling"],
                WRITING_STAGE_SYSTEM_PROMPTS["modeling"],
                modeling_prompt,
                "假设与建模",
            )
            yield _message("modeling", _preview(str(stage_outputs["modeling"])))

            yield _progress(task_id, "review", 0.3, "模型审查 Agent 正在独立审查方案", current_subtask="模型审查")
            review_prompt = (
                f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
                f"# 建模方案\n{stage_outputs['modeling']}\n\n"
                f"# 题设事实锁定\n{_json_for_prompt(problem_facts, 6000)}\n\n"
                "请给出结构化评审，不要改写原模型。"
            )
            stage_outputs["review"] = await _run_text_agent(
                task_id,
                models["review"],
                WRITING_STAGE_SYSTEM_PROMPTS["review"],
                review_prompt,
                "模型审查",
            )
            yield _message("review", _preview(str(stage_outputs["review"])))

            if _review_has_blocking_issues(str(stage_outputs["review"])):
                yield _progress(task_id, "modeling", 0.34, "模型审查发现高风险问题，正在回退修正建模", current_subtask="建模修正")
                modeling_repair_prompt = (
                    f"# 原题\n{question}\n\n"
                    f"# 当前建模方案\n{stage_outputs['modeling']}\n\n"
                    f"# 模型审查意见\n{stage_outputs['review']}\n\n"
                    f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                    "请只修正被审查明确指出的高风险定义、公式、符号、单位或可执行性问题，输出修订版建模方案。"
                )
                stage_outputs["modeling"] = await _run_text_agent(
                    task_id,
                    models["modeling"],
                    WRITING_STAGE_SYSTEM_PROMPTS["modeling"],
                    modeling_repair_prompt,
                    "建模修正",
                )
                yield _message("modeling", _preview(str(stage_outputs["modeling"])), section="建模修正")

                review_repair_prompt = (
                    f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
                    f"# 修订版建模方案\n{stage_outputs['modeling']}\n\n"
                    f"# 题设事实锁定\n{_json_for_prompt(problem_facts, 6000)}\n\n"
                    "请重新输出结构化模型审查，重点核对刚才的高风险问题是否已被修正。"
                )
                stage_outputs["review"] = await _run_text_agent(
                    task_id,
                    models["review"],
                    WRITING_STAGE_SYSTEM_PROMPTS["review"],
                    review_repair_prompt,
                    "模型审查复核",
                )
                yield _message("review", _preview(str(stage_outputs["review"])), section="模型审查复核")

            yield _progress(task_id, "solve_spec", 0.38, "求解规格 Agent 正在生成可执行规格", current_subtask="求解规格")
            solve_spec_prompt = (
                f"# 原题\n{question}\n\n"
                f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
                f"# 建模方案\n{stage_outputs['modeling']}\n\n"
                f"# 模型审查\n{stage_outputs['review']}\n\n"
                f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                "请把上述内容收敛成可执行 JSON 规格。"
                "输出必须是单个 JSON 对象，不要附加解释。"
                "JSON 顶层至少包含 subproblems 数组。每个 subproblem 至少包含：id、objective、input_files、input_columns、method、steps、expected_outputs、validation。"
                "建议为每个 subproblem 增加 priority（high/medium/low）和 complexity_hint（simple/medium/complex），用于后端分配求解预算。"
                "JSON 顶层应显式给出 requires_result_figures、requires_explanatory_figures、requires_figures。"
                "其中 requires_figures = requires_result_figures OR requires_explanatory_figures。"
                "禁止写论文式长段落，重点给出可执行步骤和可校验输出。"
            )
            solve_spec_raw = await _run_text_agent(
                task_id,
                models["modeling"],
                "你是求解规格整理 Agent，只负责把题目拆解、建模方案和审查约束转换为 solver 可执行的 JSON solve_spec。输出必须是 JSON。",
                solve_spec_prompt,
                "求解规格",
            )
            solve_spec = _extract_json_object(str(solve_spec_raw)) or _fallback_solve_spec(
                question,
                str(stage_outputs["breakdown"]),
                str(stage_outputs["modeling"]),
                str(stage_outputs["review"]),
                problem_facts,
            )
            stage_outputs["solve_spec_raw"] = solve_spec_raw
            yield _message("solve_spec", _preview(str(solve_spec_raw)), section="求解规格")

        if isinstance(solve_spec, dict):
            solve_spec["document_language"] = chart_language
            solve_spec["chart_language"] = chart_language
            solve_spec["chart_language_context"] = chart_language_context
            solve_spec["chart_language_policy"] = chart_language_policy
            requires_result_figures, requires_explanatory_figures, expected_figures = _infer_figure_requirement_flags(
                solve_spec, question, str(stage_outputs.get("breakdown", ""))
            )
            solve_spec["requires_result_figures"] = requires_result_figures
            solve_spec["requires_explanatory_figures"] = requires_explanatory_figures
            solve_spec["requires_figures"] = expected_figures
            solve_spec["expected_figures"] = expected_figures

            # Build independent FigurePlan and freeze it as the planning source of truth.
            figure_plan = FigurePlanBuilder.build(
                question=question,
                solve_spec=solve_spec,
                problem_facts=problem_facts,
                chart_language=chart_language,
                work_dir=work_dir,
            )
            figure_plan_path = FigurePlanBuilder.save(work_dir, figure_plan)
            stage_outputs["figure_plan"] = figure_plan
            stage_outputs["figure_plan_file"] = os.path.basename(figure_plan_path)

            _apply_figure_plan_to_solve_spec(solve_spec, figure_plan)
            expected_figures = bool(solve_spec.get("requires_figures"))

            figure_requests = figure_plan.get("figure_requests", []) if isinstance(figure_plan, dict) else []
            if not isinstance(figure_requests, list) or not any(isinstance(item, dict) for item in figure_requests):
                figure_requests = _ensure_figure_requests_in_solve_spec(solve_spec, chart_language)
            else:
                figure_requests = [item for item in figure_requests if isinstance(item, dict)]

            # Keep solve_spec compatibility while FigurePlan is the primary source.
            solve_spec["figure_requests"] = figure_requests
            stage_outputs["figure_requests"] = figure_requests
            stage_outputs["figure_request_count"] = len(figure_requests)

            # Upgrade ProblemContract with definitive figure requirement
            # from solve_spec (more accurate than initial heuristic)
            if expected_figures != contract.paper_requires_figures:
                contract = ProblemContract(
                    document_language=contract.document_language,
                    chart_language=contract.chart_language,
                    allowed_visible_text_language=contract.allowed_visible_text_language,
                    font_profile=contract.font_profile,
                    paper_requires_figures=expected_figures,
                    paper_requires_tables=contract.paper_requires_tables,
                    workflow_mode=contract.workflow_mode,
                    chart_language_aliases=contract.chart_language_aliases,
                    allowed_latin_tokens=contract.allowed_latin_tokens,
                )
                contract.save(work_dir)
                logger.info("ProblemContract upgraded: paper_requires_figures={}", expected_figures)

        save_json(solve_spec, solve_spec_path)
        stage_outputs["solve_spec"] = solve_spec
        stage_outputs["solve_spec_file"] = os.path.basename(solve_spec_path)

        yield _progress(task_id, "solve", 0.42, "算法与编程求解 Agent 正在执行代码", current_subtask="算法求解")
        solver_images: list[str] = []
        solver_structured_files: list[str] = []
        solver_responses: list[str] = []
        subproblems = _solve_spec_subproblems(solve_spec)

        common_solver_rules = (
            "## 核心执行顺序\n"
            "1) 先做数值计算 → 2) 立即把确认的 key_results 写入结构化结果文件 → 3) 最后才考虑出图。\n"
            "每执行一次代码得到新数值结论，就必须立即更新结构化结果文件，不要等所有子问题完成再一次性写入。\n\n"
            "若规格与实际数据不符，可以做最小必要修正，但必须在结构化结果文件 warnings 中说明。"
            "禁止反复完整读取同一工作簿或重复打印整表；完成列名识别后应转入建模与结果登记。"
            "题设锁定参数不得擅自修改。所有可写入论文的关键结果必须登记到结构化结果文件，供后续生成 result_registry.json。"
        )

        expected_figures = bool(solve_spec.get("expected_figures")) if isinstance(solve_spec, dict) else False

        # Figure protocol is NOT injected into common_solver_rules here.
        # It is injected per-subproblem based on _subproblem_figure_mode()
        # so that purely numerical subproblems never see save_paper_figure.

        if not expected_figures:
            common_solver_rules += (
                "\n## 当前任务不需要图表\n"
                "本次求解是纯数值任务，禁止生成任何图表。不要调用 save_paper_figure、plt.savefig、fig.savefig。"
                "不要 import matplotlib.pyplot 用于出图。generated_files 只允许 JSON/CSV/XLSX/TXT 文件。"
                "不要检查 save_paper_figure 是否存在或尝试探索 helper。"
            )

        common_solver_rules += (
            "\n## Tool-call discipline\n"
            "- Prefer one complete execute_code call per subproblem: load inputs, compute results, save CSV/JSON artifacts, and write the structured result file in that same call.\n"
            "- Use a second execute_code call only to fix a concrete runtime error or to repair a missing structured result file. Do not split exploration, plotting, and JSON writing across many small tool calls.\n"
            "- Once the structured result file and required artifacts exist, stop running code for that subproblem unless a blocking numerical error is found.\n"
            "\n## Solver execution discipline (HARD RULES)\n"
            "- The first 1-2 execute_code calls MUST produce the core computation script and save key_results to the structured result file. Do NOT spend tool calls on environment exploration, checking helper docs, or drawing test plots.\n"
            "- When encountering Excel column name mismatches: FIRST call execute_code to print sheet names and column headers (pd.read_excel nrows=0, .columns.tolist()), THEN write a single adapter script. Do NOT guess column names across multiple attempts.\n"
            "- When an optimization algorithm times out: IMMEDIATELY degrade to a coarse grid search or verified baseline estimate. Do NOT keep running PSO/GA/DE past the timeout. Save the coarse result as verified with a warning about precision.\n"
            "- If two consecutive execute_code calls fail with errors: switch to a minimal runnable script. Do NOT continue with incremental patches on broken code.\n"
            "\n## Formula and unit consistency rules\n"
            "- Never label a fitted slope as a physical coefficient unless the formula matches dimensionally.\n"
            "- If the model uses T = K * P * d, then K must be computed as T / (P * d). "
            "With T in N*m, P in kN, and d in mm, kN*mm equals N*m, so no extra factor is needed.\n"
            "- P / T is only a slope or conversion rate, not the torque coefficient K in T = K * P * d. "
            "If both are useful, save them with different names and formulas.\n"
            "- Every key_result for a coefficient must include formula, unit, inputs, source_data, and a warning if the unit convention is ambiguous.\n\n"
        )

        if _should_split_solver(workflow_mode, solve_spec):
            total_subproblems = len(subproblems)
            for index, subproblem in enumerate(subproblems, start=1):
                label = _subproblem_label(index, subproblem)
                subtask_title = f"算法与编程求解_{label}"
                subproblem_budget = _solver_budget_for_subproblem(budget, subproblem)
                complexity_score = _subproblem_complexity_score(subproblem)
                complexity_level = _subproblem_budget_level(complexity_score)
                yield _progress(
                    task_id,
                    "solve",
                    0.42 + min(0.12, 0.12 * index / max(total_subproblems, 1)),
                    f"算法求解 Agent 正在执行子问题 {index}/{total_subproblems}（{complexity_level}，预算 {subproblem_budget.max_total_tool_calls} 次工具调用）",
                    current_subtask=subtask_title,
                )
                sub_figure_mode = _subproblem_figure_mode(subproblem, expected_figures)
                solver = CoderAgent(
                    task_id=task_id,
                    model=models["coder"],
                    work_dir=work_dir,
                    max_chat_turns=subproblem_budget.max_chat_turns,
                    max_retries=subproblem_budget.max_retries,
                    max_total_tool_calls=subproblem_budget.max_total_tool_calls,
                    max_wall_seconds=subproblem_budget.max_wall_seconds,
                    code_interpreter=code_interpreter,
                    figure_mode=sub_figure_mode,
                )
                completed_context = (
                    f"## 已完成子问题结构化结果文件\n{', '.join(solver_structured_files) if solver_structured_files else '无'}\n\n"
                    f"## 当前工作目录结果文件\n{get_current_files(work_dir)}\n\n"
                )
                # Figure protocol injected only when this subproblem needs figures
                sub_figure_rules = ""
                if sub_figure_mode == "paper_required":
                    subproblem_id = str(subproblem.get("id") or f"problem_{index}").strip() or f"problem_{index}"
                    sub_requests = _figure_requests_for_subproblem(solve_spec, subproblem_id)
                    data_contract = _figure_data_protocol_contract(sub_requests)
                    sub_figure_rules = (
                        "生成 matplotlib 图表时，不要手动把 rcParams['font.sans-serif'] 设为 SimHei、Microsoft YaHei 或 DejaVu Sans 的单一/简化列表；优先使用环境预置字体配置。"
                        "禁止为了字体缺失、图例样式、排版美化、重复打印检查或截图式确认而重复执行出图代码；如果图表文字违反 Chart language policy 且该图会进入最终论文，允许重画一次修正语言。"
                        "若图已成功生成，只允许在其影响数值结论或文件缺失时重画；单纯视觉优化一律禁止。"
                        "每次出图前先自问：这张图的数值结论是否已经登记到结构化结果文件？如果还没有，先写结果再画图。"
                        f"\n{data_contract if data_contract else ''}"
                        f"\n## Locked chart language context from problem_facts.json\n{_json_for_prompt(chart_language_context, 4000)}\n"
                        f"\n{chart_language_policy}\n"
                        f"{_strict_chart_artifact_contract(chart_language)}\n"
                    )
                subproblem_prompt = (
                    f"## 数学建模题目\n{question}\n\n"
                    f"## 当前只要求解的子问题 {index}/{total_subproblems}\n{_json_for_prompt(subproblem, 9000)}\n\n"
                    f"## 完整 solve_spec 摘要\n{_json_for_prompt({'subproblem_count': total_subproblems, 'current_id': label, 'complexity_level': complexity_level, 'complexity_score': complexity_score, 'tool_budget': subproblem_budget.max_total_tool_calls}, 2000)}\n\n"
                    f"## problem_facts.json\n{_json_for_prompt(problem_facts, 8000)}\n\n"
                    f"## 数据文件\n{data_context}\n\n"
                    f"{completed_context}"
                    f"{common_solver_rules}"
                    f"{sub_figure_rules}"
                    "本轮预算是按当前子问题复杂度分配的；复杂度较低时应避免探索式多轮试错，复杂度较高时仍必须优先产出可写入论文的关键表格。"
                    "本轮只解决当前子问题，不要尝试完成其他子问题；若需要前序结果，只读取已完成结构化结果文件。"
                    "必须把当前子问题的 key_results、generated_files、warnings 写入本轮结构化结果文件。"
                )
                sub_result = await solver.run(prompt=subproblem_prompt, subtask_title=subtask_title)
                solver_responses.append(f"## {subtask_title}\n{sub_result.coder_response}")
                solver_images.extend(sub_result.created_images)
                solver_structured_files.extend(sub_result.structured_result_files)
                solver_structured_files = _unique_structured_result_files(solver_structured_files)
                _quarantine_unregistered_output_images(work_dir, solver_structured_files)
                partial_registry = build_result_registry(work_dir, solver_structured_files)
                save_json(partial_registry, result_registry_path)
                yield _message("solve", _preview(sub_result.coder_response), section=subtask_title)

            solver_result = CoderToWriter(
                coder_response="\n\n".join(solver_responses),
                created_images=list(dict.fromkeys(solver_images)),
                structured_result_files=solver_structured_files,
            )
        else:
            solver = CoderAgent(
                task_id=task_id,
                model=models["coder"],
                work_dir=work_dir,
                max_chat_turns=budget.solver.max_chat_turns,
                max_retries=budget.solver.max_retries,
                max_total_tool_calls=budget.solver.max_total_tool_calls,
                max_wall_seconds=budget.solver.max_wall_seconds,
                code_interpreter=code_interpreter,
                figure_mode="paper_required" if expected_figures else "none",
            )
            non_split_figure_rules = ""
            if expected_figures:
                all_requests = [item for item in (solve_spec.get("figure_requests", []) if isinstance(solve_spec, dict) else []) if isinstance(item, dict)]
                data_contract = _figure_data_protocol_contract(all_requests)
                non_split_figure_rules = (
                    f"\n{data_contract if data_contract else ''}"
                    f"\n## Locked chart language context from problem_facts.json\n{_json_for_prompt(chart_language_context, 4000)}\n"
                    f"\n{chart_language_policy}\n"
                    f"{_strict_chart_artifact_contract(chart_language)}\n"
                )
            solver_prompt = (
                f"## 数学建模题目\n{question}\n\n"
                f"## solve_spec.json\n{_json_for_prompt(solve_spec, 12000)}\n\n"
                f"## problem_facts.json\n{_json_for_prompt(problem_facts, 9000)}\n\n"
                f"## 数据文件\n{data_context}\n\n"
                f"{common_solver_rules}"
                f"{non_split_figure_rules}"
                "请严格根据 solve_spec.json 执行求解。必须按 subproblems 分步执行；每完成一个子问题，就立即把当前已确认的 key_results、generated_files 和 warnings 写回结构化结果文件，"
                "不要等全部子问题结束后再一次性落盘。若后续子问题失败，已完成子问题的结果必须保留。"
                "输出算法方案说明、完整代码、计算结果与结果摘要。"
            )
            solver_result = await solver.run(prompt=solver_prompt, subtask_title="算法与编程求解")
        stage_outputs["solve"] = solver_result.model_dump()
        solver_images = solver_result.created_images
        _quarantine_unregistered_output_images(work_dir, solver_result.structured_result_files)
        result_registry = build_result_registry(work_dir, solver_result.structured_result_files)
        save_json(result_registry, result_registry_path)
        stage_outputs["result_registry"] = result_registry
        stage_outputs["result_registry_file"] = os.path.basename(result_registry_path)
        result_driven_plan, result_driven_requests = _refresh_result_driven_figure_plan(
            work_dir=work_dir,
            solve_spec=solve_spec,
            result_registry=result_registry,
            chart_language=chart_language,
            solve_spec_path=solve_spec_path,
        )
        if result_driven_plan:
            stage_outputs["figure_plan"] = result_driven_plan
            stage_outputs["figure_plan_file"] = "figure_plan.json"
            stage_outputs["figure_requests"] = result_driven_requests
            stage_outputs["figure_request_count"] = len(result_driven_requests)
        trace.add_stage("solve", "completed",
                        f"verified={len(result_registry.get('verified_results', []) or [])}",
                        _time.monotonic() - _stage_start)
        yield _message("solve", _preview(solver_result.coder_response), section="算法与编程求解")

        verification_result = CoderToWriter(
            coder_response="当前模式跳过数值复核。",
            created_images=[],
            structured_result_files=[],
        )
        verify_plan = _build_verify_plan(result_registry)
        save_json(verify_plan, verify_plan_path)
        stage_outputs["verify_plan"] = verify_plan
        stage_outputs["verify_plan_file"] = os.path.basename(verify_plan_path)

        if workflow_mode != "fast" and _verify_plan_requires_agent(verify_plan):
            yield _progress(task_id, "verification", 0.56, "数值复核 Agent 正在按清单复核关键结果", current_subtask="数值复核")
            verification_agent = CoderAgent(
                task_id=task_id,
                model=models["coder"],
                work_dir=work_dir,
                max_chat_turns=budget.coder.max_chat_turns,
                max_retries=budget.coder.max_retries,
                max_total_tool_calls=budget.coder.max_total_tool_calls,
                max_wall_seconds=budget.coder.max_wall_seconds,
                code_interpreter=code_interpreter,
            )
            verification_agent.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["verification"]
            verification_prompt = (
                f"## verify_plan.json\n{_json_for_prompt(verify_plan, 12000)}\n\n"
                f"## problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                f"## 数据文件\n{data_context}\n\n"
                f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                "你只能复核 verify_plan.json 中列出的 items。"
                "禁止新增复核对象。禁止重新设计模型。禁止遍历整个工作目录。"
                "禁止重新读取整份原始数据做独立求解，禁止检查无关图片、目录时间戳或历史残留文件。"
                "若某个 item 本身就是 blocked 状态，只允许核对其来源结构化结果文件、result_registry.json 与 verify_plan.json 是否一致，然后直接收口。"
                "每个 item 只允许输出 verified、mismatch 或 blocked，并把逐条结论写入结构化结果文件。"
            )
            verification_result = await verification_agent.run(
                prompt=verification_prompt,
                subtask_title="数值复核",
            )
            stage_outputs["verification"] = verification_result.model_dump()
            result_registry = build_result_registry(
                work_dir,
                _unique_structured_result_files(
                    solver_result.structured_result_files,
                    verification_result.structured_result_files,
                ),
            )
            save_json(result_registry, result_registry_path)
            stage_outputs["result_registry"] = result_registry
            result_driven_plan, result_driven_requests = _refresh_result_driven_figure_plan(
                work_dir=work_dir,
                solve_spec=solve_spec,
                result_registry=result_registry,
                chart_language=chart_language,
                solve_spec_path=solve_spec_path,
            )
            if result_driven_plan:
                stage_outputs["figure_plan"] = result_driven_plan
                stage_outputs["figure_plan_file"] = "figure_plan.json"
                stage_outputs["figure_requests"] = result_driven_requests
                stage_outputs["figure_request_count"] = len(result_driven_requests)
            yield _message("verification", _preview(verification_result.coder_response), section="数值复核")
        else:
            if workflow_mode != "fast":
                verification_result = CoderToWriter(
                    coder_response="verify_plan 仅包含 blocked 项，无可独立复算的数值条目，已跳过数值复核 Agent。",
                    created_images=[],
                    structured_result_files=[],
                )
            stage_outputs["verification"] = verification_result.model_dump()

        yield _progress(task_id, "analysis", 0.68, "结果分析与验证 Agent 正在复核结论", current_subtask="结果分析")
        analysis_prompt = (
            f"# 原题\n{question}\n\n"
            f"# 模型方案\n{stage_outputs.get('modeling', '')}\n\n"
            f"# 审查报告\n{stage_outputs.get('review', '')}\n\n"
            f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
            f"# result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 10000)}\n\n"
            f"# 求解输出\n{solver_result.coder_response}\n\n"
            f"# 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
            f"# verify_plan.json\n{_json_for_prompt(verify_plan, 6000)}\n\n"
            f"# 数值复核报告\n{verification_result.coder_response}\n\n"
            f"# 数值复核结构化结果文件\n{', '.join(verification_result.structured_result_files) if verification_result.structured_result_files else '无'}\n\n"
            "请按系统提示中的固定结构输出结果分析与验证报告，并把每个关键结论回链到证据。"
        )
        stage_outputs["analysis"] = await _run_text_agent(
            task_id,
            models["analysis"],
            WRITING_STAGE_SYSTEM_PROMPTS["analysis"],
            analysis_prompt,
            "结果分析与验证",
        )
        yield _message("analysis", _preview(str(stage_outputs["analysis"])))

        chart_result = CoderToWriter(
            coder_response="复用求解阶段已生成图表，未单独运行图表 Agent。",
            created_images=solver_images,
            structured_result_files=[],
        )
        chart_images = solver_images
        if _should_run_chart_agent(workflow_mode, result_registry, solver_images, budget):
            yield _progress(task_id, "charts", 0.8, "图表与一致性 Agent 正在生成图表", current_subtask="图表生成")
            chart_agent = CoderAgent(
                task_id=task_id,
                model=models["coder"],
                work_dir=work_dir,
                max_chat_turns=budget.coder.max_chat_turns,
                max_retries=budget.coder.max_retries,
                max_total_tool_calls=budget.coder.max_total_tool_calls,
                max_wall_seconds=budget.coder.max_wall_seconds,
                code_interpreter=code_interpreter,
            )
            chart_prompt = (
                f"## 结果分析报告\n{stage_outputs['analysis']}\n\n"
                f"## result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 8000)}\n\n"
                f"## figure_requests（逐项满足 required request）\n{_json_for_prompt((solve_spec or {}).get('figure_requests', []), 8000)}\n\n"
                f"## 求解结果\n{solver_result.coder_response}\n\n"
                f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                f"## 模型符号与假设\n{stage_outputs.get('modeling', '')}\n\n"
                f"{chart_language_policy}\n"
                f"{_strict_chart_artifact_contract(chart_language)}\n"
                "图表恢复规则：如果 verified_results 为空，但 result_registry 中存在 artifact_salvage、artifact_salvage_summary、source_data、generated_files 或求解阶段图片，仍需执行仅限图表的恢复/审查，不要直接跳过。"
                "只能复用这些已登记文件和已落盘产物，禁止重新求解数学模型。"
                "逐项检查图题、坐标轴、图例、注释、色条、分类刻度是否符合 Chart language policy；只有语言错误或已有源数据可直接出图时才重绘。"
                "请优先复用求解阶段已经登记的 generated_files 和已确认结果。"
                "禁止重新设计模型、禁止重新做整题求解、禁止引入新的数值口径。"
                "只有在论文所需图表缺失且能够直接基于 verified_results 或已登记源数据补图时，才允许补充绘图。"
                "图表产物必须绑定 figure_request_id；required 图必须 semantic_verified=true。"
                "如果产物是 diagnostics（如 verified_result_summary），必须 semantic_verified=false 且 paper_section='diagnostics'。"
                "输出格式一致性校验清单；若因 verified_results 为空或证据不足而无法安全补图，必须明确写 blocked/skip 原因并直接收口。"
            )
            chart_result = await chart_agent.run(prompt=chart_prompt, subtask_title="图表与一致性")
            chart_images = chart_result.created_images
            stage_outputs["charts"] = chart_result.model_dump()
            _quarantine_unregistered_output_images(
                work_dir,
                _unique_structured_result_files(
                    solver_result.structured_result_files,
                    verification_result.structured_result_files,
                    chart_result.structured_result_files,
                ),
            )
            result_registry = build_result_registry(
                work_dir,
                _unique_structured_result_files(
                    solver_result.structured_result_files,
                    verification_result.structured_result_files,
                    chart_result.structured_result_files,
                ),
            )
            save_json(result_registry, result_registry_path)
            stage_outputs["result_registry"] = result_registry
            result_driven_plan, result_driven_requests = _refresh_result_driven_figure_plan(
                work_dir=work_dir,
                solve_spec=solve_spec,
                result_registry=result_registry,
                chart_language=chart_language,
                solve_spec_path=solve_spec_path,
            )
            if result_driven_plan:
                stage_outputs["figure_plan"] = result_driven_plan
                stage_outputs["figure_plan_file"] = "figure_plan.json"
                stage_outputs["figure_requests"] = result_driven_requests
                stage_outputs["figure_request_count"] = len(result_driven_requests)
            yield _message("charts", _preview(chart_result.coder_response), section="图表与一致性")
        else:
            verified_count = len(result_registry.get("verified_results", []) or [])
            if verified_count <= 0:
                chart_result = CoderToWriter(
                    coder_response="result_registry 中没有 verified_results，已跳过独立图表阶段，避免重复求解和引入未验证图表。",
                    created_images=[],
                    structured_result_files=[],
                )
                chart_images = []
            else:
                # verified_count > 0 但不需要单独运行图表 Agent（solver 已生成图片）
                chart_result = CoderToWriter(
                    coder_response="复用求解阶段已生成图表，未单独运行图表 Agent。",
                    created_images=solver_images,
                    structured_result_files=[],
                )
                chart_images = solver_images
            stage_outputs["charts"] = chart_result.model_dump()
            yield _message("charts", _preview(chart_result.coder_response), section="图表与一致性")

        yield _progress(task_id, "writing", 0.9, "论文组织与润色 Agent 正在整合全文", current_subtask="论文撰写")
        writer = WriterAgent(
            task_id=task_id,
            model=models["writer"],
            max_chat_turns=budget.writer.max_chat_turns,
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        writer.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["final_writer"]
        structured_result_files_for_figures = _unique_structured_result_files(
            solver_result.structured_result_files,
            verification_result.structured_result_files,
            chart_result.structured_result_files,
        )
        # Use ArtifactRegistry for figure validation (replaces _language_verified_generated_images)
        art_registry = ArtifactRegistry.load(
            work_dir,
            structured_result_files=structured_result_files_for_figures,
            contract=contract,
        )
        artifact_valid_images = art_registry.validate_for_paper()

        reevaluated = FigureStage.reevaluate_solve_spec_requests(
            solve_spec=solve_spec,
            chart_language=chart_language,
            work_dir=work_dir,
        ) if isinstance(solve_spec, dict) else []
        if reevaluated:
            stage_outputs["figure_requests"] = reevaluated
            stage_outputs["figure_request_count"] = len(reevaluated)
            save_json(solve_spec, solve_spec_path)
            figure_plan_payload = stage_outputs.get("figure_plan", {}) if isinstance(stage_outputs.get("figure_plan"), dict) else {}
            if isinstance(figure_plan_payload, dict):
                figure_plan_payload["figure_requests"] = reevaluated
                figure_plan_payload["required_feasible_count"] = solve_spec["required_feasible_count"]
                figure_plan_payload["blocked_count"] = solve_spec["blocked_count"]
                FigurePlanBuilder.save(work_dir, figure_plan_payload)
                stage_outputs["figure_plan"] = figure_plan_payload

        required_requests = _required_figure_requests(solve_spec)

        # Phase-2: deterministic-first recovery per required request.
        if required_requests:
            deterministic_report = _render_required_requests_deterministically(
                work_dir=work_dir,
                result_registry=result_registry,
                required_requests=required_requests,
                chart_language=chart_language,
            )
            if deterministic_report.get("created_images"):
                chart_images = list(dict.fromkeys([
                    *chart_images,
                    *list(deterministic_report.get("created_images", [])),
                ]))
                stage_outputs["deterministic_request_recovery"] = deterministic_report
                art_registry = ArtifactRegistry.load(
                    work_dir,
                    structured_result_files=structured_result_files_for_figures,
                    contract=contract,
                )
                artifact_valid_images = art_registry.validate_for_paper()

        semantic_bundle = FigureStage.build_bundle_snapshot(art_registry).get("figure_bundle")
        if semantic_bundle is None:
            raise ValueError("Failed to build figure bundle snapshot.")
        missing_required_requests = list(semantic_bundle.missing_requests)
        image_evidence_paths = _generated_image_evidence_paths(
            work_dir,
            structured_result_files_for_figures,
            [*solver_images, *chart_images],
        )
        if missing_required_requests:
            pre_recovery_image_evidence = [*solver_images, *chart_images]
            yield _progress(
                task_id,
                "charts",
                0.84,
                "Deterministic 渲染后仍有 required figure 缺口，正在逐项 LLM 修复。",
                current_subtask="Figure artifact repair",
            )
            recovery_round_outputs: list[dict[str, object]] = []
            # Execute one request at a time for precise failure localization.
            for missing_req in list(missing_required_requests):
                req_id = str(missing_req.get("figure_request_id") or "").strip()
                if not req_id:
                    continue

                target_req = None
                for req in required_requests:
                    if str(req.get("id") or "").strip() == req_id:
                        target_req = req
                        break
                if not target_req:
                    continue
                missing_reason = str(missing_req.get("reason") or "").strip()
                if not _repair_eligible_request(target_req, missing_reason):
                    recovery_round_outputs.append(
                        {
                            "figure_request_id": req_id,
                            "skipped": True,
                            "reason": f"preflight_blocked:{missing_reason or str(target_req.get('blocked_reason') or 'infeasible_request')}",
                        }
                    )
                    continue

                recovery_agent = CoderAgent(
                    task_id=task_id,
                    model=models["coder"],
                    work_dir=work_dir,
                    max_chat_turns=budget.coder.max_chat_turns,
                    max_retries=budget.coder.max_retries,
                    max_total_tool_calls=max(3, budget.coder.max_total_tool_calls // 2),
                    max_wall_seconds=budget.coder.max_wall_seconds,
                    code_interpreter=code_interpreter,
                )
                recovery_prompt = (
                    f"## Locked chart language context\n{_json_for_prompt(chart_language_context, 4000)}\n\n"
                    f"{chart_language_policy}\n"
                    f"{_strict_chart_artifact_contract(chart_language)}\n"
                    f"## 当前必须补齐的 figure_request\n{_json_for_prompt(target_req, 4000)}\n\n"
                    f"## Existing image evidence\n{_json_for_prompt(image_evidence_paths, 4000)}\n\n"
                    f"## result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 10000)}\n\n"
                    f"## Structured result files\n{', '.join(structured_result_files_for_figures) if structured_result_files_for_figures else 'none'}\n\n"
                    "Only repair this ONE figure_request. Do not solve unrelated requests. "
                    "Do not call plt.savefig for final paper images. Use save_paper_figure only. "
                    "The generated_files artifact for this request must include "
                    "figure_request_id/subproblem_id/semantic_role/depicts/data_bindings/semantic_verified=true."
                )
                recovery_result = await recovery_agent.run(
                    prompt=recovery_prompt,
                    subtask_title=f"Figure artifact repair: {req_id}",
                )
                recovery_round_outputs.append(
                    {
                        "figure_request_id": req_id,
                        "coder_response": recovery_result.coder_response,
                        "created_images": recovery_result.created_images,
                        "structured_result_files": recovery_result.structured_result_files,
                    }
                )

                chart_result = recovery_result
                chart_images = list(dict.fromkeys([*chart_images, *recovery_result.created_images]))
                structured_result_files_for_figures = _unique_structured_result_files(
                    structured_result_files_for_figures,
                    recovery_result.structured_result_files,
                )
                _quarantine_unregistered_output_images(work_dir, structured_result_files_for_figures)
                result_registry = build_result_registry(work_dir, structured_result_files_for_figures)
                save_json(result_registry, result_registry_path)
                stage_outputs["result_registry"] = result_registry

                art_registry = ArtifactRegistry.load(
                    work_dir,
                    structured_result_files=structured_result_files_for_figures,
                    contract=contract,
                )
                artifact_valid_images = art_registry.validate_for_paper()
                semantic_bundle = FigureStage.build_bundle_snapshot(art_registry).get("figure_bundle")
                if semantic_bundle is None:
                    raise ValueError("Failed to build figure bundle snapshot during repair loop.")
                missing_required_requests = list(semantic_bundle.missing_requests)
                if not missing_required_requests:
                    break

            stage_outputs["chart_recovery"] = {
                "mode": "per_request",
                "rounds": recovery_round_outputs,
                "remaining_missing_requests": missing_required_requests,
            }
            if recovery_round_outputs:
                yield _message(
                    "charts",
                    _preview(json.dumps(stage_outputs["chart_recovery"], ensure_ascii=False)),
                    section="Figure artifact repair",
                )
        if not artifact_valid_images:
            fallback_images = _create_verified_result_summary_figure(
                work_dir,
                result_registry,
                chart_language,
            )
            if fallback_images:
                # Re-validate after deterministic fallback
                art_registry = ArtifactRegistry.load(
                    work_dir,
                    structured_result_files=structured_result_files_for_figures,
                    contract=contract,
                )
                artifact_valid_images = art_registry.validate_for_paper()
                if artifact_valid_images:
                    chart_images = list(dict.fromkeys([*chart_images, *fallback_images]))
                    stage_outputs["deterministic_chart_recovery"] = {
                        "created_images": fallback_images,
                        "reason": "LLM chart repair produced image evidence but no verified FigureArtifact.",
                    }
        if not artifact_valid_images and _has_generated_image_evidence(
            work_dir,
            structured_result_files_for_figures,
            [*(locals().get("pre_recovery_image_evidence", [])), *solver_images, *chart_images],
        ):
            raise ValueError(
                "Generated figures exist, but none passed the strict FigureArtifact language gate. "
                "The workflow stopped instead of silently producing a paper with missing or unverified figures."
            )
        if not artifact_valid_images and _solve_spec_expects_figures(solve_spec, question, str(stage_outputs.get("breakdown", ""))):
            raise ValueError(
                "The task context indicates figures/charts are expected, but no verified paper figures were produced. "
                "The workflow stopped instead of silently producing a paper with missing figures. "
                "Ensure the solver/chart stage generates at least one figure via save_paper_figure()."
            )
        figure_gate_snapshot = FigureGateStage.from_registry(art_registry)
        artifact_valid_images = figure_gate_snapshot.artifact_valid_images
        figure_bundle = figure_gate_snapshot.figure_bundle
        writer_images = figure_gate_snapshot.writer_images
        stage_outputs["artifact_valid_images"] = artifact_valid_images
        stage_outputs["paper_ready_images"] = writer_images
        stage_outputs["figure_bundle"] = figure_gate_snapshot.figure_bundle_payload
        stage_outputs["writer_available_images"] = writer_images
        writer_context_snapshot = WriterStage.prepare_context(
            work_dir=work_dir,
            result_registry=result_registry,
            figure_bundle=figure_bundle,
            chart_language=chart_language,
        )
        writer_context_payload = writer_context_snapshot.payload
        writer_allowed_images = writer_context_snapshot.allowed_images
        stage_outputs["writer_context"] = writer_context_payload
        stage_outputs["writer_context_path"] = writer_context_snapshot.path
        stage_outputs["writer_available_images"] = writer_allowed_images
        figure_plan_payload = stage_outputs.get("figure_plan", {}) if isinstance(stage_outputs.get("figure_plan"), dict) else {}
        workflow_issues = FigureStage.normalize_workflow_issues(
            figure_plan_payload if isinstance(figure_plan_payload, dict) else None
        )
        workflow_issue_summary = FigureStage.workflow_issue_summary(workflow_issues)
        if workflow_issues:
            stage_outputs["workflow_issues"] = workflow_issues
        stage_outputs["figure_plan_diagnostics"] = {
            "requires_figures_by_problem": bool((solve_spec or {}).get("requires_figures_by_problem", False)),
            "required_feasible_count": int((solve_spec or {}).get("required_feasible_count", 0) or 0),
            "blocked_request_count": len(figure_bundle.blocked_requests),
            "blocked_requests": figure_bundle.blocked_requests,
            "recommended_request_count": len(figure_bundle.recommended_requests),
            "workflow_issue_count": int(workflow_issue_summary.get("count", 0) or 0),
            "workflow_issue_summary": workflow_issue_summary,
        }

        # --- Quality gate using ArtifactRegistry ---
        expected_figures = _solve_spec_expects_figures(solve_spec, question, str(stage_outputs.get("breakdown", "")))
        requires_result_figures = bool((solve_spec or {}).get("requires_result_figures"))
        requires_explanatory_figures = bool((solve_spec or {}).get("requires_explanatory_figures"))
        gate_passed, gate_reason = _quality_gate_before_writing(
            result_registry,
            expected_figures,
            figure_bundle.required_requests,
            figure_bundle.satisfied_requests,
            figure_bundle.missing_requests,
            workflow_mode,
            requires_result_figures=requires_result_figures,
            requires_explanatory_figures=requires_explanatory_figures,
            result_figure_count=len(figure_bundle.result_figures),
            explanatory_figure_count=len(figure_bundle.explanatory_figures),
            requires_figures_by_problem=bool((solve_spec or {}).get("requires_figures_by_problem", expected_figures)),
            blocked_requests=figure_bundle.blocked_requests,
        )

        # Generate structured gate reports from ArtifactRegistry
        figure_gate = FigureGateReport.from_rejections(
            artifact_valid_images,
            art_registry.rejection_summary(),
        )
        figure_gate.save(work_dir)
        trace.add_stage("figure_gate", "passed" if figure_gate.passed else "failed",
                        figure_gate.reason, 0,
                        {"paper_ready_count": len(writer_images),
                         "rejected_count": len(art_registry.figure_rejections)})

        quality_gate = QualityGateReport.from_check(
            verified_count=len(result_registry.get("verified_results", []) or []),
            blocked_count=len(result_registry.get("blocked_results", []) or []),
            expected_figures=expected_figures,
            paper_ready_count=len(writer_images),
            passed=gate_passed,
            reason=gate_reason,
            required_request_count=len(figure_bundle.required_requests),
            satisfied_request_count=len(figure_bundle.satisfied_requests),
            missing_requests=figure_bundle.missing_requests,
        )
        quality_gate.save(work_dir)

        if not gate_passed:
            logger.warning("Quality gate failed: {}", gate_reason)
            md_path, docx_fail_path = DocumentFinalizer.export_failure_report(
                work_dir=work_dir,
                question=question,
                result_registry=result_registry,
                gate_reason=gate_reason,
                stage_outputs=stage_outputs,
            )
            yield _message("writing", gate_reason, msg_type="error", section="质量门禁")

            failure_code = "QUALITY_GATE_FAILED"
            failure_type = "quality_gate"
            failure_details: dict[str, object] = {
                "gate_reason": gate_reason,
                "workflow_issue_count": int(workflow_issue_summary.get("count", 0) or 0),
                "workflow_issue_summary": workflow_issue_summary,
                "workflow_issues": workflow_issues[:20],
            }
            if "solve_spec 未提供 figure_requests" in gate_reason:
                failure_code = "FIGURE_PLAN_MISSING"
                figure_plan = stage_outputs.get("figure_plan", {}) if isinstance(stage_outputs, dict) else {}
                detected_needs = figure_plan.get("detected_needs", []) if isinstance(figure_plan, dict) else []
                failure_details = {
                    "reason": "FigureNeedDetector judged figures required, but FigurePlan is empty.",
                    "detected_needs": detected_needs,
                    "missing_stage": "figure_planning",
                    "repair_hint": "Run domain_template_backfill for this problem domain.",
                    "workflow_issue_count": int(workflow_issue_summary.get("count", 0) or 0),
                    "workflow_issue_summary": workflow_issue_summary,
                    "workflow_issues": workflow_issues[:20],
                }

            task_manager.update_status(task_id, TaskStatus.FAILED)
            yield _build_failed_task_result(
                task_id=task_id,
                task_type="writing",
                work_dir=work_dir,
                error_message=gate_reason,
                error_code=failure_code,
                error_type=failure_type,
                error_details=failure_details,
                paper_path=md_path,
                docx_path=docx_fail_path if docx_fail_path and os.path.exists(docx_fail_path) else "",
                notebook_path=os.path.join(work_dir, "notebook.ipynb"),
            )
            yield _progress(task_id, "done", 1.0, "质量门禁未通过，已输出失败报告", status="failed")
            return

        final_prompt = (
            f"# 原题\n{question}\n\n"
            f"# Workflow mode writing policy\n{_writer_mode_policy(workflow_mode)}\n\n"
            f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
            f"# result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 12000)}\n\n"
            f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 假设与建模\n{stage_outputs['modeling']}\n\n"
            f"# 模型审查报告\n{stage_outputs['review']}\n\n"
            f"# 算法与编程求解\n{solver_result.coder_response}\n\n"
            f"# 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
            f"# 数值复核报告\n{verification_result.coder_response}\n\n"
            f"# 数值复核结构化结果文件\n{', '.join(verification_result.structured_result_files) if verification_result.structured_result_files else '无'}\n\n"
            f"# 结果分析与验证\n{stage_outputs['analysis']}\n\n"
            f"# 图表与一致性\n{chart_result.coder_response}\n\n"
            f"# writer_context.json（Writer 唯一图表上下文）\n{_json_for_prompt(writer_context_payload, 12000)}\n\n"
            f"# FigureBundle（诊断兼容信息，正文图片仍以 writer_context.available_figures 为准）\n{_json_for_prompt(figure_bundle.to_dict(), 8000)}\n\n"
            f"{WriterStage.context_contract_text()}"
            "请输出完整论文 Markdown。论文中所有具体数值都必须来自 result_registry.json 的 verified_results；"
            "若没有对应 id 或结果处于 blocked_results，禁止写入具体数值。"
            "writer_context.explanatory_figures 必须优先插入到模型建立、符号说明、几何关系或算法流程章节。"
            "writer_context.result_figures 应插入结果分析章节。"
            "writer_context.required_images 中列出的图片路径必须在 Markdown 中逐一出现。"
            "正文只允许引用 writer_context.available_figures；禁止引用 FigureBundle.diagnostic_figures。"
            "当图的 semantic_role 为 data_overview 时，图注和正文只能描述数值变量分布/相关性概览，"
            "禁止写成拟合曲线、残差分析图或算法对比实验图。"
            "每张正文图必须保持其 figure_request_id 语义，不得用 diagnostics 图替代 required 图。"
            "若 verified_results 与 blocked_results 同时存在，只能对 blocked 条目降级，不能把已 verified 的结果整体写成未计算。"
            "参考文献必须来自 scholar 工具或用户材料。"
            "严格模式不是加长正文；严格模式必须压缩套话，把篇幅留给可复核表格、公式口径和误差/阻断说明。"
        )
        final_paper = await _run_writer_stage(
            writer=writer,
            prompt=final_prompt,
            available_images=writer_allowed_images,
            sub_title="论文组织与润色",
            budget=budget,
        )
        if not final_paper.strip():
            raise ValueError("最终写作阶段返回空内容")
        yield _message("writing", _preview(final_paper), section="论文组织与润色")

        audit_report = ""
        audit_report_machine: dict[str, object] = {"status": "SKIP", "blocks": []}
        if workflow_mode == "strict":
            delivery_auditor = CoderAgent(
                task_id=task_id,
                model=models["coder"],
                work_dir=work_dir,
                max_chat_turns=budget.coder.max_chat_turns,
                max_retries=budget.coder.max_retries,
                max_total_tool_calls=budget.coder.max_total_tool_calls,
                max_wall_seconds=budget.coder.max_wall_seconds,
                code_interpreter=code_interpreter,
            )
            delivery_auditor.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["delivery_audit"]

        max_audit_rounds = 2 if workflow_mode == "strict" else 0
        for audit_round in range(1, max_audit_rounds + 1):
            draft_paper_path = os.path.join(work_dir, f"final_paper_audit_round_{audit_round}.md")
            with open(draft_paper_path, "w", encoding="utf-8") as f:
                f.write(normalize_math_markdown(final_paper))

            audit_manifest_path = os.path.join(work_dir, f"delivery_audit_manifest_round_{audit_round}.json")
            audit_manifest = build_paper_audit_manifest(question, final_paper)
            save_json(audit_manifest, audit_manifest_path)

            yield _progress(task_id, "delivery_audit", 0.94, f"可交付终审复核 Agent 第{audit_round}轮正在抽取公式、表格并复算", current_subtask="可交付终审复核")
            delivery_audit_prompt = (
                f"## 原题\n{question}\n\n"
                f"## 题目拆解\n{stage_outputs['breakdown']}\n\n"
                f"## 建模方案\n{stage_outputs['modeling']}\n\n"
                f"## 模型审查\n{stage_outputs['review']}\n\n"
                f"## problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                f"## result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 10000)}\n\n"
                f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                f"## 数值复核结构化结果文件\n{', '.join(verification_result.structured_result_files) if verification_result.structured_result_files else '无'}\n\n"
                f"## 当前工作目录文件\n{get_current_files(work_dir)}\n\n"
                f"## 最终论文路径\n{os.path.basename(draft_paper_path)}\n\n"
                f"## 自动抽取审计清单\n{os.path.basename(audit_manifest_path)}\n\n"
                "请用代码读取最终论文和审计清单，完成公式抽取、表格数值抽取、关键结果复算、论文值与复算值比对、题设参数检查、公式编号/符号/单位检查、参考文献占位或虚构检查。"
                "若发现阻断项，必须明确写出返工路由：modeling、solver、writer。"
                "同时必须在工作目录写出 paper_audit_report.json，格式为 {status, blocks[]}，每个 block 至少包含 type、location、route、severity、fix。"
            )
            delivery_audit_result = await delivery_auditor.run(
                prompt=delivery_audit_prompt,
                subtask_title="可交付终审复核",
            )
            stage_outputs[f"delivery_audit_round_{audit_round}"] = delivery_audit_result.model_dump()
            fallback_audit_report = build_paper_audit_report(
                question,
                final_paper,
                problem_facts,
                result_registry,
                audit_manifest,
                delivery_audit_result.coder_response,
            )
            raw_machine_report = load_json(os.path.join(work_dir, "paper_audit_report.json"))
            audit_report_machine = _merge_audit_reports(raw_machine_report, fallback_audit_report)
            save_json(audit_report_machine, os.path.join(work_dir, "paper_audit_report.json"))
            stage_outputs[f"paper_audit_report_round_{audit_round}"] = audit_report_machine
            yield _message("delivery_audit", _preview(delivery_audit_result.coder_response), section=f"可交付终审复核第{audit_round}轮")

            yield _progress(task_id, "final_audit", 0.96, f"最终审查 Agent 第{audit_round}轮核对审查意见", current_subtask="最终审查")
            audit_prompt = (
                f"# 原题\n{question}\n\n"
                f"# 模型审查报告\n{stage_outputs['review']}\n\n"
                f"# 数值复核报告\n{verification_result.coder_response}\n\n"
                f"# 结果分析与验证\n{stage_outputs['analysis']}\n\n"
                f"# 图表一致性结果\n{chart_result.coder_response}\n\n"
                f"# 可交付终审复核报告\n{delivery_audit_result.coder_response}\n\n"
                f"# paper_audit_report.json\n{_json_for_prompt(audit_report_machine, 12000)}\n\n"
                f"# 可交付终审结构化结果文件\n{', '.join(delivery_audit_result.structured_result_files) if delivery_audit_result.structured_result_files else '无'}\n\n"
                f"# 最终论文\n{final_paper}\n\n"
                "请检查最终论文是否完整吸收前序审查意见与可交付终审复核结论，是否仍存在无证据结论、符号不一致、图文不一致、复核失败项被写入正文或无法验证的断言。"
            )
            audit_report = await _run_text_agent(
                task_id,
                models["review"],
                WRITING_STAGE_SYSTEM_PROMPTS["final_audit"],
                audit_prompt,
                f"最终审查第{audit_round}轮",
            )
            stage_outputs[f"final_audit_round_{audit_round}"] = audit_report
            yield _message("final_audit", _preview(audit_report), section=f"最终审查第{audit_round}轮")

            audit_first_line = audit_report.splitlines()[0].upper() if audit_report.splitlines() else ""
            delivery_first_line = delivery_audit_result.coder_response.splitlines()[0].upper() if delivery_audit_result.coder_response.splitlines() else ""
            blocked = (
                audit_report_machine.get("status") == "BLOCK"
                or bool(audit_report_machine.get("blocks"))
                or "审计结论：BLOCK" in audit_report
                or "AUDIT结论：BLOCK" in audit_report
                or "BLOCK" in audit_first_line
                or "审计结论：BLOCK" in delivery_audit_result.coder_response
                or "BLOCK" in delivery_first_line
            )
            if not blocked:
                break

            if audit_round == max_audit_rounds:
                break

            repair_routes = _routes_from_audit_report(audit_report_machine) or _detect_repair_routes(
                _json_for_prompt(audit_report_machine, 6000),
                delivery_audit_result.coder_response,
                audit_report,
            )
            stage_outputs[f"final_audit_routes_round_{audit_round}"] = repair_routes

            if "modeling" in repair_routes:
                yield _progress(task_id, "modeling", 0.972, f"建模链路正在根据第{audit_round}轮终审问题回退修正", current_subtask="终审回退-建模")
                modeling_repair_prompt = (
                    f"# 原题\n{question}\n\n"
                    f"# 当前建模方案\n{stage_outputs['modeling']}\n\n"
                    f"# 当前最终论文\n{final_paper}\n\n"
                    f"# paper_audit_report.json\n{_json_for_prompt(audit_report_machine, 10000)}\n\n"
                    f"# 可交付终审复核报告\n{delivery_audit_result.coder_response}\n\n"
                    f"# 最终审查报告\n{audit_report}\n\n"
                    "请只修正被终审指出的参数、公式、符号、单位或建模定义问题，输出新的建模方案。"
                )
                stage_outputs["modeling"] = await _run_text_agent(
                    task_id,
                    models["modeling"],
                    WRITING_STAGE_SYSTEM_PROMPTS["modeling"],
                    modeling_repair_prompt,
                    f"终审回退建模第{audit_round}轮",
                )
                yield _message("modeling", _preview(str(stage_outputs["modeling"])), section=f"终审回退建模第{audit_round}轮")

                review_repair_prompt = (
                    f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
                    f"# 新建模方案\n{stage_outputs['modeling']}\n\n"
                    f"# paper_audit_report.json\n{_json_for_prompt(audit_report_machine, 8000)}\n\n"
                    f"# 终审指出的问题\n{delivery_audit_result.coder_response}\n\n"
                    "请重新输出结构化模型审查，重点检查终审指出的问题是否已被修正。"
                )
                stage_outputs["review"] = await _run_text_agent(
                    task_id,
                    models["review"],
                    WRITING_STAGE_SYSTEM_PROMPTS["review"],
                    review_repair_prompt,
                    f"终审回退模型审查第{audit_round}轮",
                )
                yield _message("review", _preview(str(stage_outputs["review"])), section=f"终审回退模型审查第{audit_round}轮")

            if "solver" in repair_routes:
                yield _progress(task_id, "solve", 0.976, f"求解链路正在根据第{audit_round}轮终审问题回退复算", current_subtask="终审回退-求解")
                # Audit solver is for numerical repair; only inject figure mode
                # when blocks specifically mention figure/chart issues.
                # Scan the full serialized block (not just type) to catch cases
                # where type="format" but fix/location/evidence mentions figures.
                _blocks = audit_report_machine.get("blocks") or []
                _block_text = json.dumps(_blocks, ensure_ascii=False).lower() if isinstance(_blocks, list) else ""
                _has_figure_block = any(kw in _block_text for kw in ("figure", "chart", "image", "图表", "图片", "save_paper_figure", "fig.savefig", "visible_text"))
                _audit_figure_mode = "paper_required" if (expected_figures and _has_figure_block) else "none"
                solver = CoderAgent(
                    task_id=task_id,
                    model=models["coder"],
                    work_dir=work_dir,
                    max_chat_turns=budget.solver.max_chat_turns,
                    max_retries=budget.solver.max_retries,
                    max_total_tool_calls=budget.solver.max_total_tool_calls,
                    max_wall_seconds=budget.solver.max_wall_seconds,
                    code_interpreter=code_interpreter,
                    figure_mode=_audit_figure_mode,
                )
                solver_prompt = (
                    f"## 数学建模题目\n{question}\n\n"
                    f"## 题目拆解\n{stage_outputs['breakdown']}\n\n"
                    f"## 建模方案\n{stage_outputs['modeling']}\n\n"
                    f"## 模型审查意见\n{stage_outputs['review']}\n\n"
                    f"## 上一版最终论文\n{final_paper}\n\n"
                    f"## problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                    f"## paper_audit_report.json\n{_json_for_prompt(audit_report_machine, 10000)}\n\n"
                    f"## 可交付终审复核报告\n{delivery_audit_result.coder_response}\n\n"
                    f"## 最终审查报告\n{audit_report}\n\n"
                    f"## 数据文件\n{data_context}\n\n"
                    "请只针对终审阻断项重新计算、修正结果并更新结构化结果文件，禁止保留无法复现的旧结论。"
                    "必须按修复条目分步执行；每修好一类结果，就立即把当前已确认的 key_results、generated_files 和 warnings 写回结构化结果文件，"
                    "不要等全部修复完成后再统一落盘。"
                )
                solver_result = await solver.run(prompt=solver_prompt, subtask_title="算法与编程求解")
                stage_outputs["solve"] = solver_result.model_dump()
                solver_images = solver_result.created_images
                result_registry = build_result_registry(work_dir, solver_result.structured_result_files)
                save_json(result_registry, result_registry_path)
                stage_outputs["result_registry"] = result_registry
                yield _message("solve", _preview(solver_result.coder_response), section=f"终审回退算法求解第{audit_round}轮")
                artifact_context = get_current_files(work_dir)
                verify_plan = _build_verify_plan(result_registry)
                save_json(verify_plan, verify_plan_path)
                stage_outputs["verify_plan"] = verify_plan
                stage_outputs["verify_plan_file"] = os.path.basename(verify_plan_path)

                if _verify_plan_requires_agent(verify_plan):
                    verification_agent = CoderAgent(
                        task_id=task_id,
                        model=models["coder"],
                        work_dir=work_dir,
                        max_chat_turns=budget.coder.max_chat_turns,
                        max_retries=budget.coder.max_retries,
                        max_total_tool_calls=budget.coder.max_total_tool_calls,
                        max_wall_seconds=budget.coder.max_wall_seconds,
                        code_interpreter=code_interpreter,
                    )
                    verification_agent.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["verification"]
                    verification_prompt = (
                        f"## verify_plan.json\n{_json_for_prompt(verify_plan, 12000)}\n\n"
                        f"## 原题\n{question}\n\n"
                        f"## 题目拆解\n{stage_outputs['breakdown']}\n\n"
                        f"## 建模方案\n{stage_outputs['modeling']}\n\n"
                        f"## 模型审查意见\n{stage_outputs['review']}\n\n"
                        f"## result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 9000)}\n\n"
                        f"## 新求解摘要\n{solver_result.coder_response}\n\n"
                        f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                        f"## 数据文件\n{data_context}\n\n"
                        f"## 当前工作目录结果文件\n{artifact_context}\n\n"
                        f"## 可交付终审阻断项\n{delivery_audit_result.coder_response}\n\n"
                        "你只能复核 verify_plan.json 中列出的 items。"
                        "禁止新增复核对象，禁止重新设计模型，禁止遍历整个工作目录。"
                        "禁止重新读取整份原始数据做整题求解，禁止检查无关图片、目录时间戳或历史残留文件。"
                        "若某个 item 本身就是 blocked 状态，只允许核对其来源结构化结果文件、result_registry.json 与 verify_plan.json 是否一致，然后直接收口。"
                        "数值复核结构化结果文件必须输出逐条记录，明确哪些条目已修复 verified，哪些仍是 blocked 或 mismatch。"
                    )
                    verification_result = await verification_agent.run(
                        prompt=verification_prompt,
                        subtask_title="数值复核",
                    )
                else:
                    verification_result = CoderToWriter(
                        coder_response="终审回退后的 verify_plan 仅包含 blocked 项，无可独立复算的数值条目，已跳过数值复核 Agent。",
                        created_images=[],
                        structured_result_files=[],
                    )
                stage_outputs["verification"] = verification_result.model_dump()
                result_registry = build_result_registry(
                    work_dir,
                    _unique_structured_result_files(
                        solver_result.structured_result_files,
                        verification_result.structured_result_files,
                    ),
                )
                save_json(result_registry, result_registry_path)
                stage_outputs["result_registry"] = result_registry
                yield _message("verification", _preview(verification_result.coder_response), section=f"终审回退数值复核第{audit_round}轮")

                analysis_prompt = (
                    f"# 原题\n{question}\n\n"
                    f"# 模型方案\n{stage_outputs['modeling']}\n\n"
                    f"# 审查报告\n{stage_outputs['review']}\n\n"
                    f"# result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 10000)}\n\n"
                    f"# 求解输出\n{solver_result.coder_response}\n\n"
                    f"# 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                    f"# 数值复核报告\n{verification_result.coder_response}\n\n"
                    f"# 数值复核结构化结果文件\n{', '.join(verification_result.structured_result_files) if verification_result.structured_result_files else '无'}\n\n"
                    f"# 可交付终审阻断项\n{delivery_audit_result.coder_response}\n\n"
                    f"# 当前结果文件清单\n{artifact_context}\n\n"
                    "请重新输出结果分析与验证报告，并明确哪些结论已修复、哪些仍需降级。"
                )
                stage_outputs["analysis"] = await _run_text_agent(
                    task_id,
                    models["analysis"],
                    WRITING_STAGE_SYSTEM_PROMPTS["analysis"],
                    analysis_prompt,
                    f"终审回退结果分析第{audit_round}轮",
                )
                yield _message("analysis", _preview(str(stage_outputs["analysis"])), section=f"终审回退结果分析第{audit_round}轮")
                if _should_run_chart_agent("strict", result_registry, solver_images, budget):
                    chart_agent = CoderAgent(
                        task_id=task_id,
                        model=models["coder"],
                        work_dir=work_dir,
                        max_chat_turns=budget.coder.max_chat_turns,
                        max_retries=budget.coder.max_retries,
                        max_total_tool_calls=budget.coder.max_total_tool_calls,
                        max_wall_seconds=budget.coder.max_wall_seconds,
                        code_interpreter=code_interpreter,
                    )
                    chart_prompt = (
                        f"## 结果分析报告\n{stage_outputs['analysis']}\n\n"
                        f"## result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 9000)}\n\n"
                        f"## 求解结果\n{solver_result.coder_response}\n\n"
                        f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                        f"## 模型符号与假设\n{stage_outputs['modeling']}\n\n"
                        f"## 终审阻断项\n{delivery_audit_result.coder_response}\n\n"
                        f"{chart_language_policy}\n"
                        "请只修正与终审阻断项相关的图表或一致性问题。"
                        "若 verified_results 为空但存在 artifact_salvage、source_data、generated_files 或求解阶段图片，仍需执行仅限图表的恢复/审查，禁止直接跳过。"
                        "优先复用已经登记的 generated_files、source_data 和 verified_results。"
                        "禁止重新设计模型、禁止重新做整题求解、禁止引入新的数值口径。"
                        "只有在对应图表缺失且能够直接基于 verified_results 或已登记源数据补图时，才允许更新图片，并输出格式一致性校验清单。"
                    )
                    chart_result = await chart_agent.run(prompt=chart_prompt, subtask_title="图表与一致性")
                    chart_images = chart_result.created_images
                    stage_outputs["charts"] = chart_result.model_dump()
                    yield _message("charts", _preview(chart_result.coder_response), section=f"终审回退图表一致性第{audit_round}轮")
                else:
                    chart_result = CoderToWriter(
                        coder_response="终审回退后的 result_registry 中没有 verified_results，已跳过独立图表修复阶段，避免重复求解和引入未验证图表。",
                        created_images=solver_images,
                        structured_result_files=[],
                    )
                    chart_images = solver_images
                    stage_outputs["charts"] = chart_result.model_dump()
                    yield _message("charts", _preview(chart_result.coder_response), section=f"终审回退图表一致性第{audit_round}轮")

            yield _progress(task_id, "writing", 0.985, f"论文组织与润色 Agent 正在根据第{audit_round}轮终审回退重写全文", current_subtask="终审回退-写作")
            structured_result_files_for_figures = _unique_structured_result_files(
                solver_result.structured_result_files,
                verification_result.structured_result_files,
                chart_result.structured_result_files,
            )
            art_registry = ArtifactRegistry.load(
                work_dir,
                structured_result_files=structured_result_files_for_figures,
                contract=contract,
            )
            artifact_valid_images = art_registry.validate_for_paper()
            if not artifact_valid_images:
                fallback_images = _create_verified_result_summary_figure(
                    work_dir,
                    result_registry,
                    chart_language,
                )
                if fallback_images:
                    art_registry = ArtifactRegistry.load(
                        work_dir,
                        structured_result_files=structured_result_files_for_figures,
                        contract=contract,
                    )
                    artifact_valid_images = art_registry.validate_for_paper()
                    if artifact_valid_images:
                        chart_images = list(dict.fromkeys([*chart_images, *fallback_images]))
                        stage_outputs[f"deterministic_chart_recovery_round_{audit_round}"] = {
                            "created_images": fallback_images,
                            "reason": "Audit repair produced image evidence but no verified FigureArtifact.",
                        }
            if not artifact_valid_images and _has_generated_image_evidence(
                work_dir,
                structured_result_files_for_figures,
                [*solver_images, *chart_images],
            ):
                raise ValueError(
                    "Audit repair produced figures, but none passed the strict FigureArtifact language gate."
                )
            if not artifact_valid_images and _solve_spec_expects_figures(solve_spec, question, str(stage_outputs.get("breakdown", ""))):
                raise ValueError(
                    "Audit round {}: task expects figures but no verified paper figures were produced.".format(audit_round)
                )

            # Rebuild semantic bundle after audit repairs and re-run request-level quality gate.
            figure_gate_snapshot = FigureGateStage.from_registry(art_registry)
            artifact_valid_images = figure_gate_snapshot.artifact_valid_images
            figure_bundle = figure_gate_snapshot.figure_bundle
            writer_images = figure_gate_snapshot.writer_images
            stage_outputs[f"artifact_valid_images_round_{audit_round}"] = artifact_valid_images
            stage_outputs[f"paper_ready_images_round_{audit_round}"] = writer_images
            stage_outputs[f"figure_bundle_round_{audit_round}"] = figure_gate_snapshot.figure_bundle_payload
            stage_outputs[f"writer_available_images_round_{audit_round}"] = writer_images
            writer_context_snapshot = WriterStage.prepare_context(
                work_dir=work_dir,
                result_registry=result_registry,
                figure_bundle=figure_bundle,
                chart_language=chart_language,
            )
            writer_context_payload = writer_context_snapshot.payload
            writer_allowed_images = writer_context_snapshot.allowed_images
            stage_outputs[f"writer_context_round_{audit_round}"] = writer_context_payload
            stage_outputs["writer_context"] = writer_context_payload
            stage_outputs["writer_context_path"] = writer_context_snapshot.path
            stage_outputs[f"writer_available_images_round_{audit_round}"] = writer_allowed_images
            stage_outputs["writer_available_images"] = writer_allowed_images

            gate_passed, gate_reason = _quality_gate_before_writing(
                result_registry,
                _solve_spec_expects_figures(solve_spec, question, str(stage_outputs.get("breakdown", ""))),
                figure_bundle.required_requests,
                figure_bundle.satisfied_requests,
                figure_bundle.missing_requests,
                workflow_mode,
                requires_result_figures=bool((solve_spec or {}).get("requires_result_figures")),
                requires_explanatory_figures=bool((solve_spec or {}).get("requires_explanatory_figures")),
                result_figure_count=len(figure_bundle.result_figures),
                explanatory_figure_count=len(figure_bundle.explanatory_figures),
                requires_figures_by_problem=bool((solve_spec or {}).get("requires_figures_by_problem", False)),
                blocked_requests=figure_bundle.blocked_requests,
            )
            if not gate_passed:
                raise ValueError(f"Audit round {audit_round}: semantic quality gate failed: {gate_reason}")

            revision_prompt = (
                f"# 原题\n{question}\n\n"
                f"# Workflow mode writing policy\n{_writer_mode_policy(workflow_mode)}\n\n"
                f"# 已生成论文\n{final_paper}\n\n"
                f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                f"# result_registry.json\n{_json_for_prompt(_compact_result_registry_for_prompt(result_registry), 10000)}\n\n"
                f"# writer_context.json（Writer 唯一图表上下文）\n{_json_for_prompt(writer_context_payload, 12000)}\n\n"
                f"{WriterStage.context_contract_text()}"
                f"# paper_audit_report.json\n{_json_for_prompt(audit_report_machine, 12000)}\n\n"
                f"# 更新后的建模方案\n{stage_outputs['modeling']}\n\n"
                f"# 更新后的模型审查\n{stage_outputs['review']}\n\n"
                f"# 更新后的求解结果\n{solver_result.coder_response}\n\n"
                f"# 更新后的数值复核报告\n{verification_result.coder_response}\n\n"
                f"# 更新后的结果分析与验证\n{stage_outputs['analysis']}\n\n"
                f"# 更新后的图表与一致性\n{chart_result.coder_response}\n\n"
                f"# 可交付终审复核报告\n{delivery_audit_result.coder_response}\n\n"
                f"# 最终审查报告\n{audit_report}\n\n"
                f"# 返工路由\n{', '.join(repair_routes) if repair_routes else 'writer'}\n\n"
                "请严格根据终审报告与返工后的最新结果重写全文。若 verified_results 与 blocked_results 同时存在，只能对 blocked 条目降级，不能把已 verified 的结果整体写成未计算。"
                "若某问题仍无法修正，必须在正文中降级措辞，不得忽略，也不得保留与复核报告冲突的确定性表述。"
                "严格模式重写时必须删减套话和重复解释，只保留能提升数值正确性的内容。"
            )
            final_paper = await _run_writer_stage(
                writer=writer,
                prompt=revision_prompt,
                available_images=writer_allowed_images,
                sub_title=f"审查后修订第{audit_round}轮",
                budget=budget,
            )
            stage_outputs[f"writing_revision_round_{audit_round}"] = final_paper
            yield _message("writing", _preview(final_paper), section=f"审查后修订第{audit_round}轮")

        stage_outputs["final_audit"] = audit_report
        stage_outputs["paper_audit_report"] = audit_report_machine
        # Ensure stage_outputs reflects the final writer-facing image whitelist.
        stage_outputs["artifact_valid_images"] = artifact_valid_images
        stage_outputs["paper_ready_images"] = writer_images
        stage_outputs["figure_bundle"] = figure_bundle.to_dict()
        stage_outputs["writer_available_images"] = writer_images
        writer_context_snapshot = WriterStage.prepare_context(
            work_dir=work_dir,
            result_registry=result_registry,
            figure_bundle=figure_bundle,
            chart_language=chart_language,
        )
        writer_context_payload = writer_context_snapshot.payload
        writer_allowed_images = writer_context_snapshot.allowed_images
        stage_outputs["writer_context"] = writer_context_payload
        stage_outputs["writer_context_path"] = writer_context_snapshot.path
        stage_outputs["writer_available_images"] = writer_allowed_images

        paper_path, docx_path, notebook_path = await DocumentFinalizer.finalize_async(
            work_dir=work_dir,
            task_id=task_id,
            task_type="writing",
            paper_content=final_paper,
            allowed_images=writer_allowed_images,
            required_images=figure_bundle.required_images,
            result_payload={
                "question": question,
                "data_context": data_context,
                "result_coverage": result_registry.get("summary", {}),
                "stages": stage_outputs,
            },
            code_interpreter=code_interpreter,
        )

        # Save observability reports
        trace.save(work_dir)
        budget_report.save(work_dir)

        task_manager.update_status(task_id, TaskStatus.COMPLETED)
        yield _build_completed_task_result(
            task_id=task_id,
            task_type="writing",
            work_dir=work_dir,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
        )
        yield _progress(task_id, "done", 1.0, "写作任务完成", status="completed")

    except Exception as exc:
        logger.exception(f"写作工作流执行失败: {exc}")
        error_message, error_code, error_type, error_details = _classify_failure(exc)
        task_manager.update_status(task_id, TaskStatus.FAILED)
        yield _build_failed_task_result(
            task_id=task_id,
            task_type="writing",
            work_dir=work_dir,
            error_message=error_message,
            error_code=error_code,
            error_type=error_type,
            error_details=error_details,
        )
    finally:
        await code_interpreter.close()
        await scholar.close()


async def _polish_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    work_dir = task["work_dir"]
    workflow_mode = _resolve_workflow_mode(task)
    budget = resolve_workflow_budget(workflow_mode)
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    question = (task.get("source_question") or _resolve_question(task) or task.get("question", ""))
    paper_content = task.get("paper_content", "")
    if not paper_content and task.get("parent_task_id"):
        paper_content = task_manager.load_paper(task.get("parent_task_id", "")) or ""

    # ── Use ProblemContract for language (single source of truth) ──────
    contract = _resolve_polish_contract(task, workflow_mode, question, paper_content)
    contract.save(work_dir)
    chart_language = contract.chart_language
    chart_language_policy = _chart_language_policy(chart_language)
    chart_language_context = contract.chart_language_context

    requirements = task.get("polishing_requirements", "") or task.get("feedback", "")
    data_context = get_current_files(work_dir, "data")
    image_manifest = task.get("source_image_manifest", [])
    image_manifest_text = _image_manifest_text(image_manifest)

    if not paper_content.strip():
        raise ValueError("润色任务缺少论文正文，请粘贴 Markdown 内容或上传 zip/docx/pdf 论文源文件")

    models = _build_models(budget)
    code_interpreter = _build_code_interpreter(work_dir, chart_language)
    scholar = OpenAlexScholar()
    stage_outputs: dict[str, object] = {}

    try:
        source_paper_path = os.path.join(work_dir, "source_paper.md")
        if paper_content:
            with open(source_paper_path, "w", encoding="utf-8") as f:
                f.write(paper_content)

        yield _progress(task_id, "breakdown", 0.08, "题目拆解 Agent 正在整理润色目标", current_subtask="润色拆解")
        breakdown_prompt = (
            f"# 原始题目\n{question or '未提供'}\n\n"
            f"# 用户润色要求\n{requirements or '请自动识别论文中的逻辑与措辞问题'}\n\n"
            f"# 图片资源清单\n{image_manifest_text}\n\n"
            f"# 原论文\n{paper_content}\n\n"
            "请输出本次润色任务的结构化拆解。"
        )
        stage_outputs["breakdown"] = await _run_text_agent(
            task_id,
            models["breakdown"],
            POLISH_STAGE_SYSTEM_PROMPTS["breakdown"],
            breakdown_prompt,
            "润色拆解",
        )
        stage_outputs["workflow_mode"] = workflow_mode
        stage_outputs["chart_language"] = chart_language
        stage_outputs["chart_language_context"] = chart_language_context
        yield _message("breakdown", _preview(str(stage_outputs["breakdown"])))

        if workflow_mode == "fast":
            stage_outputs["consistency"] = "Fast 模式跳过模型一致性审查。"
            recalculation_result = CoderToWriter(
                coder_response="Fast 模式跳过数据计算复核。",
                created_images=[],
                structured_result_files=[],
            )
            stage_outputs["recalculation"] = recalculation_result.model_dump()
            stage_outputs["chart_consistency"] = "Fast 模式跳过图文一致性审查。"
        else:
            yield _progress(task_id, "consistency", 0.24, "模型一致性审查 Agent 正在检查全文", current_subtask="模型一致性审查")
            consistency_prompt = (
                f"# 原始题目\n{question or '未提供'}\n\n"
                f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
                f"# 图片资源清单\n{image_manifest_text}\n\n"
                f"# 原论文\n{paper_content}\n\n"
                "请给出结构化的一致性审查报告。"
            )
            stage_outputs["consistency"] = await _run_text_agent(
                task_id,
                models["review"],
                POLISH_STAGE_SYSTEM_PROMPTS["consistency"],
                consistency_prompt,
                "模型一致性审查",
            )
            yield _message("consistency", _preview(str(stage_outputs["consistency"])))

            yield _progress(task_id, "recalculation", 0.44, "数据计算复核 Agent 正在复核关键数值", current_subtask="数据计算复核")
            recalculation_agent = CoderAgent(
                task_id=task_id,
                model=models["coder"],
                work_dir=work_dir,
                max_chat_turns=budget.coder.max_chat_turns,
                max_retries=budget.coder.max_retries,
                max_total_tool_calls=budget.coder.max_total_tool_calls,
                max_wall_seconds=budget.coder.max_wall_seconds,
                code_interpreter=code_interpreter,
            )
            recalculation_agent.system_prompt = POLISH_STAGE_SYSTEM_PROMPTS["recalculation"]
            recalculation_prompt = (
                f"## 原始题目\n{question or '未提供'}\n\n"
                f"## 用户要求\n{requirements or '请自动完成论文复核'}\n\n"
                f"## 原论文\n{paper_content}\n\n"
                f"## 一致性问题\n{stage_outputs['consistency']}\n\n"
                f"## 可用数据文件\n{data_context}\n\n"
                f"## 图片资源清单\n{image_manifest_text}\n\n"
                f"{chart_language_policy}\n"
                f"{_strict_chart_artifact_contract(chart_language)}\n"
                "请重新跑表格、拟合、图像或关键数值；若图片异常且可重绘，请直接生成替代图并说明替换建议。输出必须遵守系统提示中的固定结构。"
            )
            recalculation_result = await recalculation_agent.run(
                prompt=recalculation_prompt,
                subtask_title="数据计算复核",
            )
            stage_outputs["recalculation"] = recalculation_result.model_dump()
            yield _message("recalculation", _preview(recalculation_result.coder_response), section="数据计算复核")

            yield _progress(task_id, "chart_consistency", 0.66, "图文一致性审查 Agent 正在核对图文关系", current_subtask="图文一致性审查")
            chart_review_prompt = (
                f"# 原论文\n{paper_content}\n\n"
                f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
                f"# 一致性审查\n{stage_outputs['consistency']}\n\n"
                f"# 数据复核结果\n{recalculation_result.coder_response}\n\n"
                f"# 图片资源清单\n{image_manifest_text}\n\n"
                "请输出图文一致性审查报告，逐条判断表述是否被图像、公式、表格或复核结果支持，并识别需要重绘或替换的图片。"
            )
            stage_outputs["chart_consistency"] = await _run_text_agent(
                task_id,
                models["analysis"],
                POLISH_STAGE_SYSTEM_PROMPTS["chart_review"],
                chart_review_prompt,
                "图文一致性审查",
            )
            yield _message("chart_consistency", _preview(str(stage_outputs["chart_consistency"])))

        yield _progress(task_id, "wording", 0.86, "论文措辞修订 Agent 正在输出润色稿", current_subtask="论文措辞修订")
        writer = WriterAgent(
            task_id=task_id,
            model=models["writer"],
            max_chat_turns=budget.writer.max_chat_turns,
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        writer.system_prompt = POLISH_STAGE_SYSTEM_PROMPTS["wording"]
        # Build a local result_registry from recalculation output so that
        # linked_result_ids semantic binding is enforced even in polish mode.
        polish_structured_files = _unique_structured_result_files(
            recalculation_result.structured_result_files,
        )
        polish_art_registry = ArtifactRegistry.load(
            work_dir,
            structured_result_files=polish_structured_files,
            contract=contract,
        )
        artifact_valid_images = polish_art_registry.validate_for_paper()
        if not artifact_valid_images:
            fallback_images = _create_verified_result_summary_figure(
                work_dir,
                polish_art_registry.result_registry,
                chart_language,
            )
            if fallback_images:
                polish_art_registry = ArtifactRegistry.load(
                    work_dir,
                    structured_result_files=polish_structured_files,
                    contract=contract,
                )
                artifact_valid_images = polish_art_registry.validate_for_paper()
                if artifact_valid_images:
                    stage_outputs["deterministic_chart_recovery"] = {
                        "created_images": fallback_images,
                        "reason": "Polish produced image evidence but no verified FigureArtifact.",
                    }
        if not artifact_valid_images and _has_generated_image_evidence(
            work_dir,
            recalculation_result.structured_result_files,
            recalculation_result.created_images,
        ):
            raise ValueError(
                "Polish generated replacement figures, but none passed the strict FigureArtifact language gate."
            )
        polish_gate_snapshot = FigureGateStage.from_registry(polish_art_registry)
        artifact_valid_images = polish_gate_snapshot.artifact_valid_images
        polish_figure_bundle = polish_gate_snapshot.figure_bundle
        writer_images = polish_gate_snapshot.writer_images
        stage_outputs["artifact_valid_images"] = artifact_valid_images
        stage_outputs["paper_ready_images"] = writer_images
        stage_outputs["figure_bundle"] = polish_gate_snapshot.figure_bundle_payload
        stage_outputs["writer_available_images"] = writer_images
        polish_result_registry = build_result_registry(work_dir, recalculation_result.structured_result_files)
        writer_context_snapshot = WriterStage.prepare_context(
            work_dir=work_dir,
            result_registry=polish_result_registry,
            figure_bundle=polish_figure_bundle,
            chart_language=chart_language,
        )
        writer_context_payload = writer_context_snapshot.payload
        writer_allowed_images = writer_context_snapshot.allowed_images
        stage_outputs["writer_context"] = writer_context_payload
        stage_outputs["writer_context_path"] = writer_context_snapshot.path
        stage_outputs["writer_available_images"] = writer_allowed_images
        wording_prompt = (
            f"# 原始题目\n{question or '未提供'}\n\n"
            f"# 原论文\n{paper_content}\n\n"
            f"# 用户润色要求\n{requirements or '请按竞赛论文标准润色'}\n\n"
            f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 模型一致性审查\n{stage_outputs['consistency']}\n\n"
            f"# 数据计算复核\n{recalculation_result.coder_response}\n\n"
            f"# 图文一致性审查\n{stage_outputs['chart_consistency']}\n\n"
            f"# writer_context.json（Writer 唯一图表上下文）\n{_json_for_prompt(writer_context_payload, 12000)}\n\n"
            f"{WriterStage.context_contract_text()}"
            f"# 图片资源清单\n{image_manifest_text}\n\n"
            f"# 可用替代图片\n{', '.join(recalculation_result.created_images) if recalculation_result.created_images else '无'}\n\n"
            "请输出修订后的完整 Markdown 论文；若需要替换图片，请同步更新 Markdown 图片路径。"
            "若图片语义是 data_overview，只能按变量分布/相关性概览描述，禁止写成拟合曲线或残差图。"
        )
        final_paper = await _run_writer_stage(
            writer=writer,
            prompt=wording_prompt,
            available_images=writer_allowed_images,
            sub_title="论文措辞修订",
            budget=budget,
        )
        if not final_paper.strip():
            raise ValueError("论文措辞修订阶段返回空内容")
        stage_outputs["wording"] = final_paper
        yield _message("wording", _preview(final_paper), section="论文措辞修订")

        if workflow_mode == "strict":
            yield _progress(task_id, "final_audit", 0.94, "润色终审 Agent 正在核对最终稿一致性", current_subtask="润色终审")
            final_polish_audit_prompt = (
                f"# 原始题目\n{question or '未提供'}\n\n"
                f"# 用户润色要求\n{requirements or '未提供'}\n\n"
                f"# 原论文\n{paper_content}\n\n"
                f"# 一致性审查\n{stage_outputs['consistency']}\n\n"
                f"# 数据计算复核\n{recalculation_result.coder_response}\n\n"
                f"# 图文一致性审查\n{stage_outputs['chart_consistency']}\n\n"
                f"# 最终润色稿\n{final_paper}\n\n"
                "请输出最终一致性核对结论，重点指出仍未修复的问题或确认已闭合的风险。"
            )
            stage_outputs["final_polish_audit"] = await _run_text_agent(
                task_id,
                models["review"],
                POLISH_STAGE_SYSTEM_PROMPTS["consistency"],
                final_polish_audit_prompt,
                "润色终审",
            )
            yield _message("final_audit", _preview(str(stage_outputs["final_polish_audit"])), section="润色终审")

        paper_path, docx_path, notebook_path = await DocumentFinalizer.finalize_async(
            work_dir=work_dir,
            task_id=task_id,
            task_type="polish",
            paper_content=final_paper,
            allowed_images=writer_allowed_images,
            required_images=polish_figure_bundle.required_images,
            result_payload={
                "question": question,
                "paper_content": paper_content,
                "requirements": requirements,
                "data_context": data_context,
                "stages": stage_outputs,
            },
            code_interpreter=code_interpreter,
        )

        task_manager.update_status(task_id, TaskStatus.COMPLETED)
        yield _build_completed_task_result(
            task_id=task_id,
            task_type="polish",
            work_dir=work_dir,
            paper_path=paper_path,
            docx_path=docx_path,
            notebook_path=notebook_path,
        )
        yield _progress(task_id, "done", 1.0, "润色任务完成", status="completed")

    except Exception as exc:
        logger.exception(f"润色工作流执行失败: {exc}")
        error_message, error_code, error_type, error_details = _classify_failure(exc)
        task_manager.update_status(task_id, TaskStatus.FAILED)
        yield _build_failed_task_result(
            task_id=task_id,
            task_type="polish",
            work_dir=work_dir,
            error_message=error_message,
            error_code=error_code,
            error_type=error_type,
            error_details=error_details,
        )
    finally:
        await code_interpreter.close()
        await scholar.close()


async def run_workflow(task_id: str, question: str) -> AsyncGenerator[dict, None]:
    task = task_manager.get_task(task_id)
    if not task:
        yield {"error": "任务不存在"}
        return

    task_type = task.get("task_type", "writing")
    if task_type == "polish":
        async for payload in _polish_workflow(task_id, task):
            yield payload
        return

    async for payload in _writing_workflow(task_id, task):
        yield payload


async def run_revision_workflow(
    revision_task_id: str,
    revision_task: dict,
) -> AsyncGenerator[dict, None]:
    """历史论文修订统一走润色流水线。"""
    revision_task["task_type"] = "polish"
    if not revision_task.get("paper_content") and revision_task.get("parent_task_id"):
        revision_task["paper_content"] = task_manager.load_paper(revision_task.get("parent_task_id", "")) or ""

    async for payload in _polish_workflow(revision_task_id, revision_task):
        yield payload
