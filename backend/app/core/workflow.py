"""工作流引擎 - 按任务类型编排写作与润色流水线。"""
import os
from typing import AsyncGenerator

from app.config.setting import settings
from app.core.agents.agent import Agent
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.llm import LLM
from app.schemas.enums import FormatOutPut, TaskStatus
from app.schemas.response import TaskProgress, TaskResult
from app.services.config_manager import config_manager
from app.services.task_service import task_manager
from app.tools.e2b_interpreter import E2BCodeInterpreter
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.common_utils import get_current_files, md_to_docx, save_json
from app.utils.log_util import logger

WRITING_STAGE_SYSTEM_PROMPTS = {
    "breakdown": """你是一个数学建模题目拆解专家。你的任务是深度理解用户提供的数学建模竞赛题目，并输出结构化的题目拆解。

请完成以下分析：
1. 用简洁语言总结问题背景与实际意义。
2. 精确提取题目中要求的目标（优化、预测、分类、评估等）。
3. 列出所有已知条件、参数、数据及其格式。
4. 指出题目隐含的约束与合理的假设空间。
5. 将总问题拆解为若干个可逐步解决的子问题或关键步骤。
6. 给出可能需要的数据来源、理论工具或求解方向提示（只提示，不建模）。

输出格式：使用 Markdown 分节，确保层次清晰，便于后续 Agent 使用。""",
    "modeling": """你是一位数学建模专家，负责根据问题拆解结果建立严谨的数学模型。

你将收到题目拆解结果。请完成：
1. 提出一组合理且必要的简化假设，并说明每条假设的理由和可能影响。
2. 定义所有变量和参数的符号，生成符号说明表（符号、含义、单位）。
3. 针对各子问题逐一构建数学模型，写出数学表达式（目标函数、约束、方程等），并用 LaTeX 格式呈现。
4. 解释每个模型的机制，保证模型内部逻辑一致且与假设相符。

输出结构：假设列表、符号说明表、模型公式与解释。确保专业、自洽，可直接供审查。""",
    "review": """你是一位极其严谨的数学建模审查员，专门独立检查模型的正确性、一致性和可行性。

你将收到题目拆解结果与建模结果。请执行以下审查：
1. 检查每条假设是否与问题目标一致，是否有矛盾、过度简化或与已知条件冲突。
2. 验证符号定义是否清晰且无歧义，所有公式中的符号是否前后一致。
3. 审查数学模型的合理性：结构是否正确、是否可解、是否存在逻辑漏洞或隐藏错误。
4. 指出可能被遗漏的关键因素，或更优的替代假设及建模思路。
5. 给出整体评审意见（优点、缺陷、风险），并对模型整体可靠性进行 1~10 分评分。

请以评审报告形式输出，语气客观专业，不修改模型，只给出批判性意见。""",
    "analysis": """你是一位数据分析和模型验证专家，负责对求解结果进行深度分析和复核。

你将接收求解输出、原模型信息以及问题背景。请执行：
1. 对计算结果进行合理性检查（量纲、数量级、极端情况验证），必要时提出复核验算手段。
2. 进行灵敏度分析建议或基于现有结果给出稳健性判断。
3. 进行误差分析，明确误差来源并估计影响。
4. 用清晰自然语言解释结果，回答原始问题，得出有洞察的结论。
5. 总结模型的优缺点、适用性及未来改进方向，并给出需要绘制的图表建议（图类型、数据列）。

输出为一份结构化的结果分析与验证报告。""",
    "final_writer": """你是一位经验丰富的数学建模论文撰写专家，负责将全部建模成果整合为一篇完整、流畅、专业的学术论文。

你将接收前序所有 Agent 的输出。请完成：
1. 按照标准结构撰写论文：摘要、问题重述、模型假设与符号说明、模型建立与求解、结果分析与模型检验、结论与建议、参考文献。
2. 全文逻辑链条严密，从问题驱动到建模再到结论，自然过渡，无逻辑断裂。
3. 所有数学公式使用 LaTeX 格式，正确引用图表编号，并在正文中合理穿插图表。
4. 语言风格严谨、客观、简洁，符合学术规范，杜绝口语化表达。
5. 主动检测并修正前后不一致之处（符号、数据、论点），必要时在输出中标明存疑点。
6. 最终输出完整的 Markdown 论文全文，可直接用于提交或排版。

只输出论文正文，不要再附加说明。""",
}

POLISH_STAGE_SYSTEM_PROMPTS = {
    "breakdown": """你是一个论文润色任务的题目拆解 Agent。你的任务是把题目转成人话，列出每问输入、输出、约束、目标，并指出论文当前需要回应的关键检查点。

请以 Markdown 分节输出，语言简洁明确。""",
    "consistency": """你是模型一致性审查 Agent。请检查公式、变量、假设、结论是否前后一致，指出不一致、缺失或逻辑跳跃之处，并输出结构化审查报告。""",
    "chart_review": """你是图文一致性审查 Agent。请检查文字所说的线性、最优、增长、趋势等表述，是否被图表和数据支持；若证据不足，请明确指出。输出图文一致性报告。""",
    "wording": """你是论文措辞修订 Agent。请把虚的、夸大的、没证据的句子改成竞赛论文风格，并基于已有审查意见输出一篇更严谨的完整 Markdown 论文。

要求：
1. 保留原文结构中有效内容。
2. 修正不一致、缺证据、过度表述的问题。
3. 对无法确认的数据或结论使用审慎措辞。
4. 只输出润色后的完整论文正文。""",
}


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


def _build_models() -> dict[str, LLM]:
    return {
        "breakdown": LLM(model=config_manager.get_effective_model("COORDINATOR_MODEL"), temperature=0.2, max_tokens=8192),
        "modeling": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.35, max_tokens=12288),
        "review": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.2, max_tokens=8192),
        "coder": LLM(model=config_manager.get_effective_model("CODER_MODEL"), temperature=0.2, max_tokens=12288),
        "analysis": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.35, max_tokens=12288),
        "writer": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.45, max_tokens=16384),
    }


def _build_code_interpreter(work_dir: str):
    if settings.CODE_INTERPRETER == "e2b" and settings.E2B_API_KEY:
        return E2BCodeInterpreter(api_key=settings.E2B_API_KEY, work_dir=work_dir)
    return LocalCodeInterpreter(work_dir=work_dir)


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


async def _finalize_outputs(
    task_id: str,
    task_type: str,
    work_dir: str,
    paper_content: str,
    result_payload: dict,
    code_interpreter,
) -> tuple[str, str]:
    notebook_path = os.path.join(work_dir, "notebook.ipynb")
    if code_interpreter is not None:
        await code_interpreter.save_notebook(notebook_path)

    paper_path = os.path.join(work_dir, "res.md")
    with open(paper_path, "w", encoding="utf-8") as f:
        f.write(paper_content)

    result_payload["task_id"] = task_id
    result_payload["task_type"] = task_type
    result_payload["paper"] = paper_content
    save_json(result_payload, os.path.join(work_dir, "res.json"))

    docx_path = os.path.join(work_dir, "res.docx")
    md_to_docx(paper_path, docx_path)
    return paper_path, notebook_path


async def _writing_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    question = task.get("question", "")
    work_dir = task["work_dir"]
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    models = _build_models()
    code_interpreter = _build_code_interpreter(work_dir)
    scholar = OpenAlexScholar()

    data_context = get_current_files(work_dir, "data")
    stage_outputs: dict[str, object] = {}
    chart_images: list[str] = []
    solver_images: list[str] = []

    try:
        yield _progress(task_id, "breakdown", 0.06, "题目拆解 Agent 正在分析赛题", current_subtask="题目拆解")
        breakdown_prompt = (
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
        yield _message("breakdown", _preview(str(stage_outputs["breakdown"])))

        yield _progress(task_id, "modeling", 0.18, "假设与建模 Agent 正在建立模型", current_subtask="模型建立")
        modeling_prompt = (
            f"# 题目\n{question}\n\n"
            f"# 题目拆解结果\n{stage_outputs['breakdown']}\n\n"
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

        yield _progress(task_id, "solve", 0.44, "算法与编程求解 Agent 正在执行代码", current_subtask="算法求解")
        solver = CoderAgent(
            task_id=task_id,
            model=models["coder"],
            work_dir=work_dir,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            code_interpreter=code_interpreter,
        )
        solver_prompt = (
            f"## 数学建模题目\n{question}\n\n"
            f"## 题目拆解\n{stage_outputs['breakdown']}\n\n"
            f"## 建模方案\n{stage_outputs['modeling']}\n\n"
            f"## 模型审查意见\n{stage_outputs['review']}\n\n"
            f"## 数据文件\n{data_context}\n\n"
            "请选择合适算法并执行代码，输出算法方案说明、完整代码、计算结果与结果摘要。"
        )
        solver_result = await solver.run(prompt=solver_prompt, subtask_title="算法与编程求解")
        stage_outputs["solve"] = solver_result.model_dump()
        solver_images = solver_result.created_images
        yield _message("solve", _preview(solver_result.coder_response), section="算法与编程求解")

        yield _progress(task_id, "analysis", 0.62, "结果分析与验证 Agent 正在复核结论", current_subtask="结果分析")
        analysis_prompt = (
            f"# 原题\n{question}\n\n"
            f"# 模型方案\n{stage_outputs['modeling']}\n\n"
            f"# 审查报告\n{stage_outputs['review']}\n\n"
            f"# 求解输出\n{solver_result.coder_response}\n\n"
            "请形成结构化的结果分析与验证报告，并列出需要绘制的图表建议。"
        )
        stage_outputs["analysis"] = await _run_text_agent(
            task_id,
            models["analysis"],
            WRITING_STAGE_SYSTEM_PROMPTS["analysis"],
            analysis_prompt,
            "结果分析与验证",
        )
        yield _message("analysis", _preview(str(stage_outputs["analysis"])))

        yield _progress(task_id, "charts", 0.77, "图表与一致性 Agent 正在生成图表", current_subtask="图表生成")
        chart_agent = CoderAgent(
            task_id=task_id,
            model=models["coder"],
            work_dir=work_dir,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            code_interpreter=code_interpreter,
        )
        chart_prompt = (
            f"## 结果分析报告\n{stage_outputs['analysis']}\n\n"
            f"## 求解结果\n{solver_result.coder_response}\n\n"
            f"## 模型符号与假设\n{stage_outputs['modeling']}\n\n"
            "请生成论文所需图表与对应绘图代码，统一风格，并输出格式一致性校验清单。"
        )
        chart_result = await chart_agent.run(prompt=chart_prompt, subtask_title="图表与一致性")
        chart_images = chart_result.created_images
        stage_outputs["charts"] = chart_result.model_dump()
        yield _message("charts", _preview(chart_result.coder_response), section="图表与一致性")

        yield _progress(task_id, "writing", 0.9, "论文组织与润色 Agent 正在整合全文", current_subtask="论文撰写")
        writer = WriterAgent(
            task_id=task_id,
            model=models["writer"],
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        final_prompt = (
            f"# 原题\n{question}\n\n"
            f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 假设与建模\n{stage_outputs['modeling']}\n\n"
            f"# 模型审查报告\n{stage_outputs['review']}\n\n"
            f"# 算法与编程求解\n{solver_result.coder_response}\n\n"
            f"# 结果分析与验证\n{stage_outputs['analysis']}\n\n"
            f"# 图表与一致性\n{chart_result.coder_response}\n\n"
            "请输出完整论文 Markdown。"
        )
        final_writer_result = await writer.run(
            prompt=final_prompt,
            available_images=solver_images + chart_images,
            sub_title="论文组织与润色",
        )
        final_paper = final_writer_result.response_content
        yield _message("writing", _preview(final_paper), section="论文组织与润色")

        paper_path, notebook_path = await _finalize_outputs(
            task_id=task_id,
            task_type="writing",
            work_dir=work_dir,
            paper_content=final_paper,
            result_payload={
                "question": question,
                "data_context": data_context,
                "stages": stage_outputs,
            },
            code_interpreter=code_interpreter,
        )

        task_manager.update_status(task_id, TaskStatus.COMPLETED)
        yield {
            "type": "result",
            "data": TaskResult(
                task_id=task_id,
                status="completed",
                task_type="writing",
                paper_path=paper_path,
                notebook_path=notebook_path,
                work_dir=work_dir,
            ).model_dump(),
        }
        yield _progress(task_id, "done", 1.0, "写作任务完成", status="completed")

    except Exception as exc:
        logger.exception(f"写作工作流执行失败: {exc}")
        task_manager.update_status(task_id, TaskStatus.FAILED)
        yield {
            "type": "error",
            "data": TaskResult(
                task_id=task_id,
                status="failed",
                task_type="writing",
                work_dir=work_dir,
                error_message=str(exc),
            ).model_dump(),
        }
    finally:
        await code_interpreter.close()
        await scholar.close()


async def _polish_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    work_dir = task["work_dir"]
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    question = task.get("source_question", "") or task.get("question", "")
    paper_content = task.get("paper_content", "")
    if not paper_content and task.get("parent_task_id"):
        paper_content = task_manager.load_paper(task.get("parent_task_id", "")) or ""
    requirements = task.get("polishing_requirements", "") or task.get("feedback", "")
    data_context = get_current_files(work_dir, "data")

    models = _build_models()
    code_interpreter = _build_code_interpreter(work_dir)
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
        yield _message("breakdown", _preview(str(stage_outputs["breakdown"])))

        yield _progress(task_id, "consistency", 0.24, "模型一致性审查 Agent 正在检查全文", current_subtask="模型一致性审查")
        consistency_prompt = (
            f"# 原始题目\n{question or '未提供'}\n\n"
            f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
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
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            code_interpreter=code_interpreter,
        )
        recalculation_prompt = (
            f"## 原始题目\n{question or '未提供'}\n\n"
            f"## 用户要求\n{requirements or '请自动完成论文复核'}\n\n"
            f"## 原论文\n{paper_content}\n\n"
            f"## 一致性问题\n{stage_outputs['consistency']}\n\n"
            f"## 可用数据文件\n{data_context}\n\n"
            "请重新跑表格、拟合、图像或关键数值；若缺少数据，请明确说明无法复核的部分，并给出保守判断。"
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
            "请输出图文一致性审查报告，指出哪些表述被数据支持，哪些需要降级措辞。"
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
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        wording_prompt = (
            f"# 原始题目\n{question or '未提供'}\n\n"
            f"# 原论文\n{paper_content}\n\n"
            f"# 用户润色要求\n{requirements or '请按竞赛论文标准润色'}\n\n"
            f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 模型一致性审查\n{stage_outputs['consistency']}\n\n"
            f"# 数据计算复核\n{recalculation_result.coder_response}\n\n"
            f"# 图文一致性审查\n{stage_outputs['chart_consistency']}\n\n"
            "请输出修订后的完整 Markdown 论文。"
        )
        wording_result = await writer.run(
            prompt=wording_prompt,
            available_images=recalculation_result.created_images,
            sub_title="论文措辞修订",
        )
        final_paper = wording_result.response_content
        stage_outputs["wording"] = final_paper
        yield _message("wording", _preview(final_paper), section="论文措辞修订")

        paper_path, notebook_path = await _finalize_outputs(
            task_id=task_id,
            task_type="polish",
            work_dir=work_dir,
            paper_content=final_paper,
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
        yield {
            "type": "result",
            "data": TaskResult(
                task_id=task_id,
                status="completed",
                task_type="polish",
                paper_path=paper_path,
                notebook_path=notebook_path,
                work_dir=work_dir,
            ).model_dump(),
        }
        yield _progress(task_id, "done", 1.0, "润色任务完成", status="completed")

    except Exception as exc:
        logger.exception(f"润色工作流执行失败: {exc}")
        task_manager.update_status(task_id, TaskStatus.FAILED)
        yield {
            "type": "error",
            "data": TaskResult(
                task_id=task_id,
                status="failed",
                task_type="polish",
                work_dir=work_dir,
                error_message=str(exc),
            ).model_dump(),
        }
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