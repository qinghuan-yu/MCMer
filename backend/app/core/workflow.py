"""
工作流引擎 - 协调 Multi-Agent 协作
"""
import os
from typing import AsyncGenerator

from app.config.setting import settings
from app.core.llm.llm import LLM
from app.core.agents import (
    CoordinatorAgent,
    ModelerAgent,
    CoderAgent,
    WriterAgent,
)
from app.core.flows import Flows
from app.schemas.A2A import (
    CoordinatorToModeler,
    ModelerToCoder,
    CoderToWriter,
    WriterResponse,
)
from app.schemas.enums import TaskStatus, FormatOutPut
from app.schemas.response import SystemMessage, TaskProgress, TaskResult
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.e2b_interpreter import E2BCodeInterpreter
from app.tools.openalex_scholar import OpenAlexScholar
from app.services.config_manager import config_manager
from app.services.redis_manager import redis_manager
from app.services.task_service import task_manager
from app.utils.log_util import logger
from app.utils.common_utils import save_json, md_to_docx
from app.core.agents.modeler_agent import repair_json


def _build_revision_plan_fallback(feedback: str, revise_code: bool) -> dict:
    """当 LLM 规划失败时，使用规则兜底的修订计划。"""
    lowered = feedback.lower()
    code_keywords = [
        "代码", "算法", "参数", "仿真", "复现", "python", "模型", "训练", "求解", "代码手",
    ]
    needs_code = revise_code or any(k in lowered for k in code_keywords)
    return {
        "impacted_sections": ["摘要", "模型建立", "模型求解", "结果分析"],
        "requires_coder": needs_code,
        "coder_targets": ["重跑关键实验并更新图表", "核验参数与结果一致性"] if needs_code else [],
        "writer_instructions": "优先最小改动，仅修改受影响章节并保持其余结构不变。",
    }


async def _plan_revision(
    task_id: str,
    model: LLM,
    original_question: str,
    original_paper: str,
    feedback: str,
    revise_code: bool,
) -> dict:
    """生成章节级修订计划：受影响章节、是否需要代码重跑、代码目标。"""
    planner = ModelerAgent(task_id=task_id, model=model)
    planner_prompt = (
        "你是论文修订规划器。请根据题目、原论文和用户反馈，输出 JSON 计划。\n"
        "JSON 必须包含键：\n"
        "- impacted_sections: string[]\n"
        "- requires_coder: boolean\n"
        "- coder_targets: string[]\n"
        "- writer_instructions: string\n"
        "不要输出 Markdown，不要输出解释文字。\n\n"
        f"原始题目:\n{original_question}\n\n"
        f"用户反馈:\n{feedback}\n\n"
        f"是否用户显式要求改代码: {str(revise_code)}\n\n"
        f"原论文摘要（截断）:\n{original_paper[:4000]}"
    )

    await planner.append_chat_history({"role": "system", "content": "你只输出合法 JSON。"})
    await planner.append_chat_history({"role": "user", "content": planner_prompt})

    response = await planner.model.chat(
        history=planner.chat_history,
        agent_name="RevisionPlanner",
    )
    content = response.choices[0].message.content if response.choices else ""
    parsed = repair_json(content or "") if content else None

    if not isinstance(parsed, dict):
        return _build_revision_plan_fallback(feedback, revise_code)

    impacted_sections = parsed.get("impacted_sections", [])
    coder_targets = parsed.get("coder_targets", [])
    requires_coder = bool(parsed.get("requires_coder", False) or revise_code)
    writer_instructions = str(
        parsed.get("writer_instructions")
        or "优先最小改动，保持论文整体结构与语气一致。"
    )

    if not isinstance(impacted_sections, list):
        impacted_sections = []
    if not isinstance(coder_targets, list):
        coder_targets = []

    return {
        "impacted_sections": [str(s) for s in impacted_sections if str(s).strip()],
        "requires_coder": requires_coder,
        "coder_targets": [str(s) for s in coder_targets if str(s).strip()],
        "writer_instructions": writer_instructions,
    }


async def run_workflow(
    task_id: str,
    question: str,
) -> AsyncGenerator[dict, None]:
    """
    运行完整的 Multi-Agent 工作流

    Yields:
        进度消息 (dict)
    """
    task_info = task_manager.get_task(task_id)
    if not task_info:
        yield {"error": "任务不存在"}
        return

    work_dir = task_info["work_dir"]
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    # ========================================
    # 初始化各 Agent 的 LLM
    # ========================================
    coordinator_model = LLM(
        model=config_manager.get_effective_model("COORDINATOR_MODEL"),
        temperature=0.3,
    )
    modeler_model = LLM(
        model=config_manager.get_effective_model("MODELER_MODEL"),
        temperature=0.5,
    )
    coder_model = LLM(
        model=config_manager.get_effective_model("CODER_MODEL"),
        temperature=0.3,
        max_tokens=8192,
    )
    writer_model = LLM(
        model=config_manager.get_effective_model("WRITER_MODEL"),
        temperature=0.7,
        max_tokens=16384,
    )

    # ========================================
    # 初始化 Code Interpreter
    # ========================================
    code_interpreter = None
    if settings.CODE_INTERPRETER == "e2b" and settings.E2B_API_KEY:
        code_interpreter = E2BCodeInterpreter(
            api_key=settings.E2B_API_KEY, work_dir=work_dir
        )
    else:
        code_interpreter = LocalCodeInterpreter(work_dir=work_dir)

    # ========================================
    # 初始化 Scholar
    # ========================================
    scholar = OpenAlexScholar()

    try:
        # ========================================
        # Stage 1: 协调手 - 分析题目
        # ========================================
        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="running",
                stage="coordinator",
                progress=0.05,
                message="协调手正在分析题目...",
            ).model_dump(),
        }

        coordinator = CoordinatorAgent(
            task_id=task_id,
            model=coordinator_model,
        )
        coordinator_result: CoordinatorToModeler = await coordinator.run(question)

        coordinator_titles = [
            sub_question.title
            for sub_question in coordinator_result.questions.sub_questions[:5]
        ]
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                type="success",
                agent="coordinator",
                content=(
                    "协调手已完成拆题："
                    + ("；".join(coordinator_titles) if coordinator_titles else "未返回明确子问题")
                ),
            ),
        )

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="running",
                stage="coordinator",
                progress=0.1,
                message=f"题目已拆解为 {coordinator_result.ques_count} 个子问题",
            ).model_dump(),
        }

        # ========================================
        # Stage 2: 建模手 - 设计建模方案
        # ========================================
        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="running",
                stage="modeler",
                progress=0.15,
                message="建模手正在设计建模方案...",
            ).model_dump(),
        }

        modeler = ModelerAgent(
            task_id=task_id,
            model=modeler_model,
        )
        modeler_result: ModelerToCoder = await modeler.run(coordinator_result)

        modeler_sections = list(modeler_result.questions_solution.keys())[:6]
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                type="success",
                agent="modeler",
                content=(
                    "建模手已输出方案："
                    + ("、".join(modeler_sections) if modeler_sections else "未返回章节方案")
                ),
            ),
        )

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="running",
                stage="modeler",
                progress=0.25,
                message=f"已完成 {len(modeler_result.questions_solution)} 个建模方案",
            ).model_dump(),
        }

        # ========================================
        # Stage 3: 两阶段章节编排
        # - 阶段1: 求解阶段 (Coder + Writer)
        # - 阶段2: 写作阶段 (Writer only)
        # ========================================
        coder = CoderAgent(
            task_id=task_id,
            model=coder_model,
            work_dir=work_dir,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            code_interpreter=code_interpreter,
        )

        coder_results: list[CoderToWriter] = []

        writer = WriterAgent(
            task_id=task_id,
            model=writer_model,
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )

        flows = Flows()
        section_results: dict[str, str] = {}

        # -----------------------------
        # 阶段1: solution flows
        # -----------------------------
        solution_flows = flows.get_solution_flows(coordinator_result, modeler_result)
        total_solution = max(1, len(solution_flows))
        solved = 0

        for section_key, section_meta in solution_flows.items():
            yield {
                "type": "progress",
                "data": TaskProgress(
                    task_id=task_id,
                    status="running",
                    stage="coder",
                    progress=0.25 + 0.35 * (solved / total_solution),
                    message=f"代码手执行章节: {section_key}",
                    current_subtask=section_key,
                ).model_dump(),
            }

            coder_result = await coder.run(
                prompt=section_meta["coder_prompt"],
                subtask_title=section_key,
            )
            coder_results.append(coder_result)

            writer_prompt = flows.get_solution_writer_prompt(
                section_key=section_key,
                section_meta=section_meta,
                coder_result=coder_result,
                coordinator=coordinator_result,
            )
            writer_result = await writer.run(
                prompt=writer_prompt,
                available_images=coder_result.created_images,
                sub_title=section_key,
            )
            section_results[section_key] = writer_result.response_content
            solved += 1

        # -----------------------------
        # 阶段2: write flows
        # -----------------------------
        write_flows = flows.get_write_flows(
            question=question,
            coordinator=coordinator_result,
            solution_sections=section_results,
        )
        total_write = max(1, len(write_flows))
        written = 0

        for section_key, writer_prompt in write_flows.items():
            yield {
                "type": "progress",
                "data": TaskProgress(
                    task_id=task_id,
                    status="running",
                    stage="writer",
                    progress=0.72 + 0.2 * (written / total_write),
                    message=f"写作手撰写章节: {section_key}",
                    current_subtask=section_key,
                ).model_dump(),
            }

            writer_result = await writer.run(
                prompt=writer_prompt,
                available_images=[],
                sub_title=section_key,
            )
            section_results[section_key] = writer_result.response_content
            written += 1

        # 保存 notebook
        notebook_path = os.path.join(work_dir, "notebook.ipynb")
        await code_interpreter.save_notebook(notebook_path)

        full_paper = flows.build_markdown(section_results)

        # 保存论文
        paper_path = os.path.join(work_dir, "res.md")
        with open(paper_path, "w", encoding="utf-8") as f:
            f.write(full_paper)

        # 保存结构化结果
        save_json(
            {
                "task_id": task_id,
                "question": question,
                "coordinator": coordinator_result.model_dump(),
                "modeler": modeler_result.model_dump(),
                "coder_results": [c.model_dump() for c in coder_results],
                "sections": section_results,
                "paper": full_paper,
            },
            os.path.join(work_dir, "res.json"),
        )

        # 可选输出 docx（依赖 pandoc）
        docx_path = os.path.join(work_dir, "res.docx")
        md_to_docx(paper_path, docx_path)

        # 参考文献由写作内容中的 markdown 引用承载（保留兼容位置）

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="running",
                stage="writer",
                progress=0.95,
                message="论文撰写完成",
            ).model_dump(),
        }

        # ========================================
        # 完成
        # ========================================
        task_manager.update_status(task_id, TaskStatus.COMPLETED)

        yield {
            "type": "result",
            "data": TaskResult(
                task_id=task_id,
                status="completed",
                paper_path=paper_path,
                notebook_path=notebook_path,
                work_dir=work_dir,
            ).model_dump(),
        }

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=task_id,
                status="completed",
                stage="done",
                progress=1.0,
                message="🎉 数学建模任务完成！",
            ).model_dump(),
        }

    except Exception as e:
        logger.exception(f"工作流执行失败: {e}")
        task_manager.update_status(task_id, TaskStatus.FAILED)

        yield {
            "type": "error",
            "data": TaskResult(
                task_id=task_id,
                status="failed",
                work_dir=work_dir,
                error_message=str(e),
            ).model_dump(),
        }

    finally:
        await code_interpreter.close()
        await scholar.close()


# ============================================================
# 论文修订工作流 (Human-in-Loop)
# ============================================================

async def run_revision_workflow(
    revision_task_id: str,
    revision_task: dict,
) -> AsyncGenerator[dict, None]:
    """
    运行论文修订工作流 - 用户提出修改建议，Agent 修订论文

    流程:
    1. 加载原始任务的论文和上下文
    2. 根据用户反馈，让写作手（或代码手+写作手）修订论文
    3. 保存修订版本
    """
    parent_task_id = revision_task.get("parent_task_id", "")
    work_dir = revision_task["work_dir"]
    revision_version = revision_task.get("revision_number", 1)

    task_manager.update_status(revision_task_id, TaskStatus.RUNNING)

    # 加载原始论文
    original_paper = task_manager.load_paper(parent_task_id)
    if not original_paper:
        yield {
            "type": "error",
            "data": TaskResult(
                task_id=revision_task_id,
                status="failed",
                work_dir=work_dir,
                error_message="无法加载原始论文",
            ).model_dump(),
        }
        return

    # 加载原始任务上下文
    original_ctx = task_manager.load_task_context(parent_task_id)
    original_question = original_ctx.get("question", "") if original_ctx else ""

    # 用户的修订建议
    feedback = revision_task.get("feedback", "") or revision_task.get("question", "")
    # 兼容旧格式: "原始问题|||修订建议"
    if "|||" in feedback:
        feedback = feedback.split("|||", 1)[1]
    revise_code = revision_task.get("revise_code", False)

    yield {
        "type": "progress",
        "data": TaskProgress(
            task_id=revision_task_id,
            status="running",
            stage="revision",
            progress=0.1,
            message="正在分析修订建议...",
        ).model_dump(),
    }

    # ========================================
    # 初始化修订用的 LLM
    # ========================================
    writer_model = LLM(
        model=config_manager.get_effective_model("WRITER_MODEL"),
        temperature=0.5,
        max_tokens=16384,
    )

    coder_model = LLM(
        model=config_manager.get_effective_model("CODER_MODEL"),
        temperature=0.3,
        max_tokens=8192,
    )

    scholar = OpenAlexScholar()

    try:
        # Step 0: 章节级修订规划
        revision_plan = await _plan_revision(
            task_id=revision_task_id,
            model=writer_model,
            original_question=original_question,
            original_paper=original_paper,
            feedback=feedback,
            revise_code=bool(revise_code),
        )
        impacted_sections = revision_plan.get("impacted_sections", [])
        requires_coder = bool(revision_plan.get("requires_coder", False) or revise_code)
        coder_targets = revision_plan.get("coder_targets", [])
        writer_instructions = revision_plan.get("writer_instructions", "")

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=revision_task_id,
                status="running",
                stage="revision",
                progress=0.16,
                message=(
                    f"修订规划完成：影响章节 {len(impacted_sections)} 个，"
                    f"{'需要' if requires_coder else '无需'}重跑代码"
                ),
            ).model_dump(),
        }

        # ========================================
        # Step 1: 如果需要修改代码，让代码手执行
        # ========================================
        revised_code_results = ""
        if requires_coder:
            yield {
                "type": "progress",
                "data": TaskProgress(
                    task_id=revision_task_id,
                    status="running",
                    stage="revision",
                    progress=0.2,
                    message="代码手正在根据修订建议重新执行代码...",
                ).model_dump(),
            }

            code_interpreter = LocalCodeInterpreter(work_dir=work_dir)
            coder = CoderAgent(
                task_id=revision_task_id,
                model=coder_model,
                work_dir=work_dir,
                max_chat_turns=settings.MAX_CHAT_TURNS,
                max_retries=settings.MAX_RETRIES,
                code_interpreter=code_interpreter,
            )

            code_prompt = (
                f"## 原始题目\n{original_question}\n\n"
                f"## 用户修订建议\n{feedback}\n\n"
                f"## 规划器建议修改章节\n{', '.join(impacted_sections) or '未指定'}\n\n"
                f"## 规划器代码目标\n"
                f"{' ; '.join(coder_targets) if coder_targets else '根据反馈自主判断并说明'}\n\n"
                f"## 原始论文（摘要部分）\n{original_paper[:3000]}\n\n"
                f"请根据用户的修订建议，重新编写并执行相关代码。"
                f"如果只需要修改论文文字而非代码，说明即可。"
            )

            coder_result = await coder.run(
                prompt=code_prompt,
                subtask_title="修订代码",
            )
            revised_code_results = coder_result.coder_response

            notebook_path = os.path.join(work_dir, "notebook.ipynb")
            await code_interpreter.save_notebook(notebook_path)
            await code_interpreter.close()

            yield {
                "type": "progress",
                "data": TaskProgress(
                    task_id=revision_task_id,
                    status="running",
                    stage="revision",
                    progress=0.4,
                    message="代码修订完成，正在修订论文...",
                ).model_dump(),
            }

        # ========================================
        # Step 2: 让写作手修订论文
        # ========================================
        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=revision_task_id,
                status="running",
                stage="revision",
                progress=0.5,
                message="写作手正在根据建议修订论文...",
            ).model_dump(),
        }

        writer = WriterAgent(
            task_id=revision_task_id,
            model=writer_model,
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
            max_memory=30,
        )

        # 构建修订提示
        revision_prompt = (
            f"## 任务：修订数学建模论文\n\n"
            f"### 原始题目\n{original_question}\n\n"
            f"### 用户修订建议\n{feedback}\n\n"
            f"### 规划器识别的影响章节\n{', '.join(impacted_sections) or '未指定'}\n\n"
            f"### 规划器写作要求\n{writer_instructions or '优先最小改动并保持结构稳定'}\n\n"
        )

        if revised_code_results:
            revision_prompt += (
                f"### 修订后的代码执行结果\n{revised_code_results}\n\n"
            )

        revision_prompt += (
            f"### 原始论文（请基于此修订）\n\n```markdown\n{original_paper}\n```\n\n"
            f"---\n\n"
            f"## 修订要求\n\n"
            f"请根据用户的修订建议，对原始论文进行修改。要求：\n"
            f"1. **保留原有结构和正确的部分**，不要重写整篇论文\n"
            f"2. **只修改用户提到的部分**，其他内容保持不变\n"
            f"3. 如果建议涉及模型改进，更新对应的建模与求解章节\n"
            f"4. 如果建议涉及结果分析，更新模型分析章节\n"
            f"5. 在论文开头的摘要中体现修改内容\n"
            f"6. 保持学术论文的正式语言风格\n"
            f"7. 使用 Markdown 格式，数学公式使用 LaTeX\n\n"
            f"请输出修订后的完整论文："
        )

        writer_result: WriterResponse = await writer.run(
            prompt=revision_prompt,
            available_images=[],
            sub_title=f"论文修订 v{revision_version}",
        )

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=revision_task_id,
                status="running",
                stage="revision",
                progress=0.85,
                message="正在保存修订版论文...",
            ).model_dump(),
        }

        # 保存修订版论文到原任务目录
        paper_path = task_manager.save_revision(
            parent_task_id, writer_result.response_content, revision_version
        )

        # 同时保存到修订任务目录
        local_paper_path = os.path.join(work_dir, "res.md")
        with open(local_paper_path, "w", encoding="utf-8") as f:
            f.write(writer_result.response_content)

        # 保存修订结构化结果
        save_json(
            {
                "task_id": revision_task_id,
                "original_task_id": parent_task_id,
                "revision_version": revision_version,
                "feedback": feedback,
                "revise_code": revise_code,
                "revision_plan": revision_plan,
                "writer": writer_result.model_dump(),
            },
            os.path.join(work_dir, "res.json"),
        )

        # 可选输出 docx
        md_to_docx(local_paper_path, os.path.join(work_dir, "res.docx"))

        # ========================================
        # 完成
        # ========================================
        task_manager.update_status(revision_task_id, TaskStatus.COMPLETED)
        task_manager.update_paper_path(revision_task_id, paper_path)

        yield {
            "type": "result",
            "data": TaskResult(
                task_id=revision_task_id,
                status="completed",
                paper_path=paper_path,
                notebook_path=os.path.join(work_dir, "notebook.ipynb"),
                work_dir=work_dir,
            ).model_dump(),
        }

        yield {
            "type": "progress",
            "data": TaskProgress(
                task_id=revision_task_id,
                status="completed",
                stage="done",
                progress=1.0,
                message=f"🎉 论文修订完成！版本 v{revision_version} 已保存",
            ).model_dump(),
        }

    except Exception as e:
        logger.exception(f"修订工作流执行失败: {e}")
        task_manager.update_status(revision_task_id, TaskStatus.FAILED)
        yield {
            "type": "error",
            "data": TaskResult(
                task_id=revision_task_id,
                status="failed",
                work_dir=work_dir,
                error_message=str(e),
            ).model_dump(),
        }
    finally:
        await scholar.close()
