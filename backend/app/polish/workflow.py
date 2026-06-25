"""Independent polish workflow boundary."""

from __future__ import annotations

import os
from typing import AsyncGenerator

from app.artifacts.contracts import ProblemContract
from app.artifacts.registry import ArtifactRegistry
from app.core.agents.agent import Agent
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.llm import LLM
from app.core.prompts import POLISH_STAGE_SYSTEM_PROMPTS
from app.core.stages.figure_gate_stage import FigureGateStage
from app.core.stages.finalizer_stage import FinalizerStage
from app.core.stages.writer_stage import WriterStage
from app.core.workflow import (
    _build_code_interpreter,
    _build_completed_task_result,
    _build_failed_task_result,
    _build_models,
    _chart_language_policy,
    _classify_failure,
    _create_verified_result_summary_figure,
    _has_generated_image_evidence,
    _image_manifest_text,
    _json_for_prompt,
    _message,
    _preview,
    _progress,
    _resolve_question,
    _resolve_workflow_mode,
    _run_writer_stage,
    _strict_chart_artifact_contract,
    _unique_structured_result_files,
)
from app.schemas.A2A import CoderToWriter
from app.schemas.enums import FormatOutPut, TaskStatus
from app.services.task_service import task_manager
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.common_utils import build_result_registry, get_current_files
from app.utils.log_util import logger


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

async def run_polish_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
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

        finalized_output = await FinalizerStage.finalize_success(
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
        paper_path = finalized_output.paper_path
        docx_path = finalized_output.docx_path
        notebook_path = finalized_output.notebook_path

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

