"""writing support responsibilities."""

from __future__ import annotations

from app.core.agents.writer_agent import WriterAgent
from app.core.workflow_budget import WorkflowBudget, normalize_workflow_mode
from app.utils.paper_audit import validate_paper_audit_report
from app.utils.log_util import logger


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
