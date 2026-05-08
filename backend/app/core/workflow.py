"""工作流引擎 - 按任务类型编排写作与润色流水线。"""
import json
import os
import re
from typing import AsyncGenerator

from app.config.setting import settings
from app.core.agents.agent import Agent
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.llm import LLM
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
    get_current_files,
    load_json,
    md_to_docx,
    normalize_math_markdown,
    save_json,
    validate_paper_audit_report,
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


def _build_models() -> dict[str, LLM]:
    return {
        "breakdown": LLM(model=config_manager.get_effective_model("COORDINATOR_MODEL"), temperature=0.2, max_tokens=8192),
        "modeling": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.35, max_tokens=12288),
        "review": LLM(model=config_manager.get_effective_model("MODELER_MODEL"), temperature=0.2, max_tokens=8192),
        "coder": LLM(model=config_manager.get_effective_model("CODER_MODEL"), temperature=0.2, max_tokens=12288),
        "analysis": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.35, max_tokens=12288),
        "writer": LLM(model=config_manager.get_effective_model("WRITER_MODEL"), temperature=0.45, max_tokens=16384),
    }


def _resolve_workflow_mode(task: dict) -> str:
    mode = str(task.get("workflow_mode") or settings.WORKFLOW_MODE or "standard").strip().lower()
    return mode if mode in {"fast", "standard", "strict"} else "standard"


def _review_has_blocking_issues(review_text: str) -> bool:
    keywords = ["无法求解", "定义缺失", "公式错误", "符号不一致", "逻辑跳跃", "证据不足"]
    text = str(review_text or "")
    return any(keyword in text for keyword in keywords)


def _build_verify_plan(result_registry: dict[str, object]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for entry in (result_registry.get("verified_results", []) or []) + (result_registry.get("blocked_results", []) or []):
        if not isinstance(entry, dict):
            continue
        value = entry.get("value", "")
        paper_value = entry.get("paper_value", "")
        computed_value = entry.get("computed_value", "")
        if value in {"", None} and paper_value in {"", None} and computed_value in {"", None}:
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


def _should_run_chart_agent(
    workflow_mode: str,
    result_registry: dict[str, object],
    solver_images: list[str],
) -> bool:
    verified_count = len(result_registry.get("verified_results", []) or [])
    if verified_count <= 0:
        return False
    return workflow_mode == "strict" or not solver_images


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
) -> tuple[str, str, str]:
    notebook_path = os.path.join(work_dir, "notebook.ipynb")
    if code_interpreter is not None:
        await code_interpreter.save_notebook(notebook_path)

    normalized_paper = normalize_math_markdown(paper_content)

    paper_path = os.path.join(work_dir, "res.md")
    with open(paper_path, "w", encoding="utf-8") as f:
        f.write(normalized_paper)

    result_payload["task_id"] = task_id
    result_payload["task_type"] = task_type
    result_payload["paper"] = normalized_paper
    save_json(result_payload, os.path.join(work_dir, "res.json"))

    docx_path = os.path.join(work_dir, "res.docx")
    docx_generated = md_to_docx(paper_path, docx_path)
    return paper_path, docx_path if docx_generated else "", notebook_path


async def _writing_workflow(task_id: str, task: dict) -> AsyncGenerator[dict, None]:
    question = _resolve_question(task)
    work_dir = task["work_dir"]
    workflow_mode = _resolve_workflow_mode(task)
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    if not question.strip():
        raise ValueError("写作任务缺少原始题目内容，请输入题目或上传 docx/pdf 原题文件")

    models = _build_models()
    code_interpreter = _build_code_interpreter(work_dir)
    scholar = OpenAlexScholar()

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
        problem_facts = build_problem_facts(question, str(stage_outputs["breakdown"]))
        save_json(problem_facts, problem_facts_path)
        stage_outputs["problem_facts"] = problem_facts
        stage_outputs["problem_facts_file"] = os.path.basename(problem_facts_path)
        stage_outputs["workflow_mode"] = workflow_mode
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

        save_json(solve_spec, solve_spec_path)
        stage_outputs["solve_spec"] = solve_spec
        stage_outputs["solve_spec_file"] = os.path.basename(solve_spec_path)

        yield _progress(task_id, "solve", 0.42, "算法与编程求解 Agent 正在执行代码", current_subtask="算法求解")
        solver = CoderAgent(
            task_id=task_id,
            model=models["coder"],
            work_dir=work_dir,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            max_wall_seconds=settings.SOLVE_CODER_MAX_WALL_SECONDS,
            code_interpreter=code_interpreter,
        )
        solver_prompt = (
            f"## 数学建模题目\n{question}\n\n"
            f"## solve_spec.json\n{_json_for_prompt(solve_spec, 12000)}\n\n"
            f"## problem_facts.json\n{_json_for_prompt(problem_facts, 9000)}\n\n"
            f"## 数据文件\n{data_context}\n\n"
            "请严格根据 solve_spec.json 执行求解；若规格与实际数据不符，可以做最小必要修正，但必须在结构化结果文件 warnings 中说明。"
            "必须按 subproblems 分步执行；每完成一个子问题，就立即把当前已确认的 key_results、generated_files 和 warnings 写回结构化结果文件，"
            "不要等全部子问题结束后再一次性落盘。若后续子问题失败，已完成子问题的结果必须保留。"
            "输出算法方案说明、完整代码、计算结果与结果摘要。"
            "题设锁定参数不得擅自修改。所有可写入论文的关键结果必须登记到结构化结果文件，供后续生成 result_registry.json。"
        )
        solver_result = await solver.run(prompt=solver_prompt, subtask_title="算法与编程求解")
        stage_outputs["solve"] = solver_result.model_dump()
        solver_images = solver_result.created_images
        result_registry = build_result_registry(work_dir, solver_result.structured_result_files)
        save_json(result_registry, result_registry_path)
        stage_outputs["result_registry"] = result_registry
        stage_outputs["result_registry_file"] = os.path.basename(result_registry_path)
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
                max_chat_turns=settings.MAX_CHAT_TURNS,
                max_retries=settings.MAX_RETRIES,
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
                list(dict.fromkeys(solver_result.structured_result_files + verification_result.structured_result_files)),
            )
            save_json(result_registry, result_registry_path)
            stage_outputs["result_registry"] = result_registry
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
            f"# result_registry.json\n{_json_for_prompt(result_registry, 10000)}\n\n"
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
            created_images=[],
            structured_result_files=[],
        )
        if _should_run_chart_agent(workflow_mode, result_registry, solver_images):
            yield _progress(task_id, "charts", 0.8, "图表与一致性 Agent 正在生成图表", current_subtask="图表生成")
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
                f"## result_registry.json\n{_json_for_prompt(result_registry, 8000)}\n\n"
                f"## 求解结果\n{solver_result.coder_response}\n\n"
                f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                f"## 模型符号与假设\n{stage_outputs.get('modeling', '')}\n\n"
                "请优先复用求解阶段已经登记的 generated_files 和已确认结果。"
                "禁止重新设计模型、禁止重新做整题求解、禁止引入新的数值口径。"
                "只有在论文所需图表缺失且能够直接基于 verified_results 或已登记源数据补图时，才允许补充绘图。"
                "输出格式一致性校验清单；若因 verified_results 为空或证据不足而无法安全补图，必须明确写 blocked/skip 原因并直接收口。"
            )
            chart_result = await chart_agent.run(prompt=chart_prompt, subtask_title="图表与一致性")
            chart_images = chart_result.created_images
            stage_outputs["charts"] = chart_result.model_dump()
            result_registry = build_result_registry(
                work_dir,
                list(
                    dict.fromkeys(
                        solver_result.structured_result_files
                        + verification_result.structured_result_files
                        + chart_result.structured_result_files
                    )
                ),
            )
            save_json(result_registry, result_registry_path)
            stage_outputs["result_registry"] = result_registry
            yield _message("charts", _preview(chart_result.coder_response), section="图表与一致性")
        else:
            verified_count = len(result_registry.get("verified_results", []) or [])
            if verified_count <= 0:
                chart_result = CoderToWriter(
                    coder_response="result_registry 中没有 verified_results，已跳过独立图表阶段，避免重复求解和引入未验证图表。",
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
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        writer.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["final_writer"]
        final_prompt = (
            f"# 原题\n{question}\n\n"
            f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
            f"# result_registry.json\n{_json_for_prompt(result_registry, 12000)}\n\n"
            f"# 题目拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 假设与建模\n{stage_outputs['modeling']}\n\n"
            f"# 模型审查报告\n{stage_outputs['review']}\n\n"
            f"# 算法与编程求解\n{solver_result.coder_response}\n\n"
            f"# 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
            f"# 数值复核报告\n{verification_result.coder_response}\n\n"
            f"# 数值复核结构化结果文件\n{', '.join(verification_result.structured_result_files) if verification_result.structured_result_files else '无'}\n\n"
            f"# 结果分析与验证\n{stage_outputs['analysis']}\n\n"
            f"# 图表与一致性\n{chart_result.coder_response}\n\n"
            "请输出完整论文 Markdown。论文中所有具体数值都必须来自 result_registry.json 的 verified_results；"
            "若没有对应 id 或结果处于 blocked_results，禁止写入具体数值。参考文献必须来自 scholar 工具或用户材料。"
        )
        final_writer_result = await writer.run(
            prompt=final_prompt,
            available_images=solver_images + chart_images,
            sub_title="论文组织与润色",
        )
        final_paper = final_writer_result.response_content
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
                max_chat_turns=settings.MAX_CHAT_TURNS,
                max_retries=settings.MAX_RETRIES,
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
                f"## result_registry.json\n{_json_for_prompt(result_registry, 10000)}\n\n"
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
                        max_chat_turns=settings.MAX_CHAT_TURNS,
                        max_retries=settings.MAX_RETRIES,
                        code_interpreter=code_interpreter,
                    )
                    verification_agent.system_prompt = WRITING_STAGE_SYSTEM_PROMPTS["verification"]
                    verification_prompt = (
                        f"## verify_plan.json\n{_json_for_prompt(verify_plan, 12000)}\n\n"
                        f"## 原题\n{question}\n\n"
                        f"## 题目拆解\n{stage_outputs['breakdown']}\n\n"
                        f"## 建模方案\n{stage_outputs['modeling']}\n\n"
                        f"## 模型审查意见\n{stage_outputs['review']}\n\n"
                        f"## result_registry.json\n{_json_for_prompt(result_registry, 9000)}\n\n"
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
                    list(dict.fromkeys(solver_result.structured_result_files + verification_result.structured_result_files)),
                )
                save_json(result_registry, result_registry_path)
                stage_outputs["result_registry"] = result_registry
                yield _message("verification", _preview(verification_result.coder_response), section=f"终审回退数值复核第{audit_round}轮")

                analysis_prompt = (
                    f"# 原题\n{question}\n\n"
                    f"# 模型方案\n{stage_outputs['modeling']}\n\n"
                    f"# 审查报告\n{stage_outputs['review']}\n\n"
                    f"# result_registry.json\n{_json_for_prompt(result_registry, 10000)}\n\n"
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
                if _should_run_chart_agent("strict", result_registry, solver_images):
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
                        f"## result_registry.json\n{_json_for_prompt(result_registry, 9000)}\n\n"
                        f"## 求解结果\n{solver_result.coder_response}\n\n"
                        f"## 求解结构化结果文件\n{', '.join(solver_result.structured_result_files) if solver_result.structured_result_files else '无'}\n\n"
                        f"## 模型符号与假设\n{stage_outputs['modeling']}\n\n"
                        f"## 终审阻断项\n{delivery_audit_result.coder_response}\n\n"
                        "请只修正与终审阻断项相关的图表或一致性问题。"
                        "优先复用已经登记的 generated_files 和 verified_results。"
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
            revision_prompt = (
                f"# 原题\n{question}\n\n"
                f"# 已生成论文\n{final_paper}\n\n"
                f"# problem_facts.json\n{_json_for_prompt(problem_facts, 7000)}\n\n"
                f"# result_registry.json\n{_json_for_prompt(result_registry, 10000)}\n\n"
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
                "请严格根据终审报告与返工后的最新结果重写全文。若某问题仍无法修正，必须在正文中降级措辞，不得忽略，也不得保留与复核报告冲突的确定性表述。"
            )
            revised_writer_result = await writer.run(
                prompt=revision_prompt,
                available_images=solver_images + chart_images,
                sub_title=f"审查后修订第{audit_round}轮",
            )
            final_paper = revised_writer_result.response_content
            stage_outputs[f"writing_revision_round_{audit_round}"] = final_paper
            yield _message("writing", _preview(final_paper), section=f"审查后修订第{audit_round}轮")

        stage_outputs["final_audit"] = audit_report
        stage_outputs["paper_audit_report"] = audit_report_machine

        paper_path, docx_path, notebook_path = await _finalize_outputs(
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
                docx_path=docx_path,
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
    workflow_mode = _resolve_workflow_mode(task)
    task_manager.update_status(task_id, TaskStatus.RUNNING)

    question = (task.get("source_question") or _resolve_question(task) or task.get("question", ""))
    paper_content = task.get("paper_content", "")
    if not paper_content and task.get("parent_task_id"):
        paper_content = task_manager.load_paper(task.get("parent_task_id", "")) or ""
    requirements = task.get("polishing_requirements", "") or task.get("feedback", "")
    data_context = get_current_files(work_dir, "data")
    image_manifest = task.get("source_image_manifest", [])
    image_manifest_text = _image_manifest_text(image_manifest)

    if not paper_content.strip():
        raise ValueError("润色任务缺少论文正文，请粘贴 Markdown 内容或上传 zip/docx/pdf 论文源文件")

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
                max_chat_turns=settings.MAX_CHAT_TURNS,
                max_retries=settings.MAX_RETRIES,
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
            format_output=FormatOutPut.Markdown,
            scholar=scholar,
        )
        writer.system_prompt = POLISH_STAGE_SYSTEM_PROMPTS["wording"]
        wording_prompt = (
            f"# 原始题目\n{question or '未提供'}\n\n"
            f"# 原论文\n{paper_content}\n\n"
            f"# 用户润色要求\n{requirements or '请按竞赛论文标准润色'}\n\n"
            f"# 任务拆解\n{stage_outputs['breakdown']}\n\n"
            f"# 模型一致性审查\n{stage_outputs['consistency']}\n\n"
            f"# 数据计算复核\n{recalculation_result.coder_response}\n\n"
            f"# 图文一致性审查\n{stage_outputs['chart_consistency']}\n\n"
            f"# 图片资源清单\n{image_manifest_text}\n\n"
            f"# 可用替代图片\n{', '.join(recalculation_result.created_images) if recalculation_result.created_images else '无'}\n\n"
            "请输出修订后的完整 Markdown 论文；若需要替换图片，请同步更新 Markdown 图片路径。"
        )
        wording_result = await writer.run(
            prompt=wording_prompt,
            available_images=recalculation_result.created_images,
            sub_title="论文措辞修订",
        )
        final_paper = wording_result.response_content
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

        paper_path, docx_path, notebook_path = await _finalize_outputs(
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
                docx_path=docx_path,
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