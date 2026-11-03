"""coder agent responsibilities."""

from __future__ import annotations

from app.core.agents.agent import Agent
from app.config.setting import settings
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, InterpreterMessage
from app.tools.base_interpreter import BaseCodeInterpreter
from app.core.llm.llm import LLM
from app.schemas.A2A import CoderToWriter
from app.core.prompts import CODER_PROMPT, get_reflection_prompt
from app.utils.file_io import get_current_files
from app.utils.figure_artifacts import is_image_path, normalize_artifact_path
from pathlib import Path
import json
import re
import time
from app.core.functions import coder_tools
from app.core.agents.coder_artifacts import _dedupe_jsonish_items, _installed_package_summary, _list_existing_artifacts, _list_existing_image_evidence, _looks_like_non_mutating_summary_code, _salvage_artifact_key_results, _salvage_existing_payload_key_results, _sanitize_section_name, _validate_structured_result_file
from app.core.agents.coder_chart_language import _chart_language_guard_message, _chart_language_from_prompt, _chinese_chart_label_literals, _english_chart_label_literals


class CoderAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        work_dir: str,
        max_chat_turns: int = settings.MAX_CHAT_TURNS,
        max_retries: int = settings.MAX_RETRIES,
        max_total_tool_calls: int = settings.CODER_MAX_TOTAL_TOOL_CALLS,
        max_wall_seconds: int = settings.CODER_MAX_WALL_SECONDS,
        code_interpreter: BaseCodeInterpreter = None,
        figure_mode: str = "paper_required",
    ) -> None:
        super().__init__(task_id, model, max_chat_turns)
        self.work_dir = work_dir
        self.max_retries = max_retries
        self.max_total_tool_calls = max_total_tool_calls
        self.max_wall_seconds = max_wall_seconds
        self.is_first_run = True
        self.system_prompt = CODER_PROMPT
        self.code_interpreter = code_interpreter
        self.last_executed_code = ""
        self.same_code_repeat_count = 0
        # figure_mode controls what figure-related code is allowed:
        #   "none"           — no figures at all (numeric-only tasks)
        #   "diagnostic_only" — only debug_artifacts/ diagnostics allowed
        #   "paper_required"  — full FigureArtifact contract (default)
        if figure_mode not in {"none", "diagnostic_only", "paper_required"}:
            figure_mode = "paper_required"
        self.figure_mode = figure_mode


    def _structured_result_filename(self, subtask_title: str) -> str:
        return f"{_sanitize_section_name(subtask_title)}_structured_results.json"









    async def _handle_completion_loop(
        self,
        code: str,
        subtask_title: str,
        expected_result_file: Path,
        tool_id: str,
    ) -> bool:
        is_valid, _ = _validate_structured_result_file(expected_result_file, subtask_title)
        if not is_valid:
            return False

        if not _looks_like_non_mutating_summary_code(code):
            return False

        logger.warning("检测到已完成任务仍在执行只读收尾脚本，强制要求模型直接输出最终结论")
        await self.append_chat_history(
            {
                "role": "tool",
                "tool_call_id": tool_id,
                "name": "execute_code",
                "content": (
                    f"{expected_result_file.name} 已存在且校验通过。"
                    "你本轮请求的代码只是在读取现有结果并打印检查/总结，不会生成新的结果文件。"
                    "不要再调用 execute_code；请直接输出最终结果摘要、关键结论、生成文件和风险提示。"
                ),
            }
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "停止执行收尾检查或最终总结代码。"
                    "当前子任务已经具备有效的结构化结果文件，请直接给出最终自然语言结论，"
                    "不要继续调用 execute_code。"
                ),
            }
        )
        return True



    async def _ensure_structured_result_contract(self, subtask_title: str) -> None:
        result_filename = self._structured_result_filename(subtask_title)
        installed_packages = _installed_package_summary()
        contract_prompt = (
            f"当前子任务必须遵守以下执行契约：\n"
            f"1. 只能使用当前环境已安装的库；已知可用包示例：{installed_packages}\n"
            "2. 如果需要第三方库但环境未安装，必须立刻改用标准库、已安装库或已有结果文件，不能继续依赖不存在的包。\n"
            f"3. 在结束前，必须在工作目录写出结构化结果文件 {result_filename}。\n"
            "4. 该 JSON 文件必须包含字段：section、summary、key_results、generated_files、warnings。\n"
            f"5. section 字段必须严格等于 {subtask_title}。\n"
            "6. key_results 必须是数组，且不能为空。\n"
            "7. 若当前子任务不是数值复核，则每个 key_result 至少应包含 id、name、value、unit、formula、inputs、source_data、code_cell、evidence、source、verified、status、warnings。\n"
            "8. 若当前子任务是数值复核，则每个 key_result 至少应包含 id、name、paper_value、computed_value 或 value、unit、source_data、verified、status、warnings。\n"
            "9. 对 status=verified 的条目，禁止使用 见论文/无法确认 这类占位值；必须给出真实 value 和 source_data。\n"
            "10. generated_files 只能列出最终可交付产物，不能把结构化结果 JSON 自身列入 generated_files；最终图表必须显式列出相对路径，例如 output/final_chart.png。\n"
            "13. 若结果不可靠，必须写入 warnings，并在 key_results 中显式标注 mismatch 或 blocked。"
        )
        if self.figure_mode == "paper_required":
            contract_prompt += (
                "\n11. Final paper figures must be saved with save_paper_figure(fig, filename, visible_text_audit=[...], linked_result_ids=[...], source_data=[...]). Do not use plt.savefig(...) for deliverable images; plt.savefig is only allowed for debug_artifacts/ diagnostics with paper_ready=false.\n"
                "12. Strict FigureArtifact rule: every image in generated_files must be an object, not a bare path string. Final/paper images must include path, kind='figure', paper_ready=true, visible_text_language, chart_language_verified=true, visible_text_audit, created_by='save_paper_figure', helper_version>=3, linked_result_ids, and source_data. Temporary or diagnostic images must be paper_ready=false and saved under debug_artifacts/.\n"
            )
        elif self.figure_mode == "none":
            contract_prompt += (
                "\n11. 当前子任务是纯数值任务，禁止生成任何图表。不要调用 save_paper_figure、plt.savefig、fig.savefig 或任何图像保存函数。generated_files 只允许 JSON/CSV/XLSX/TXT 文件。\n"
                "12. 禁止 import matplotlib.pyplot 或任何绘图库用于生成图表。数值计算和结构化结果是唯一目标。\n"
            )
        await self.append_chat_history({"role": "user", "content": contract_prompt})

    async def _soft_reset_retry_limit(self, last_error_message: str):
        logger.warning(f"达到最大尝试次数，重置计数并继续: {self.max_retries}")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="代码手达到最大反思次数，已切换为保守求解策略继续执行",
                type="warning",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "你已经多次因为同一类错误反复失败。不要重复之前的失败方案。"
                    "请基于现有上下文继续完成当前子任务，优先采用更稳健、更简化、更少依赖的实现路径。"
                    "只有在原始子任务要求已经全部满足时，才停止调用工具并输出最终结论。"
                    f"最后一次错误信息如下：{last_error_message}"
                ),
            }
        )

    async def _soft_reset_chat_limit(self):
        logger.warning(f"达到最大聊天次数，重置计数并继续: {self.max_chat_turns}")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="代码手达到轮次上限，已压缩思路并继续完成当前子任务",
                type="warning",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "你已经进行了很多轮对话。"
                    "请停止重复尝试，基于当前已有结果收敛到一个可执行且可交付的方案，"
                    "必要时采用更简单的近似、降级实现或分步完成策略。"
                    "但只有在原始子任务要求已经全部满足时，才输出最终结果并停止调用工具。"
                ),
            }
        )

    async def _warn_near_tool_budget(
        self,
        total_tool_calls: int,
        subtask_title: str,
        expected_result_file: Path,
    ) -> None:
        remaining = self.max_total_tool_calls - total_tool_calls
        minimum_delivery_contract = (
            "最小可交付结果必须包含："
            "1) 已经算出的关键数值表或参数表；"
            "2) 每个数值对应的 source_data；"
            "3) 已生成文件清单；"
            "4) 未完成项和原因。"
            "如果完整模型尚未完成，也要先交付这些已确认结果，禁止把整题概括为全部未计算。"
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"代码手接近工具调用上限，剩余 {remaining} 次，将进入最小数值交付模式。",
                type="warning",
                agent="coder",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    f"你距离工具调用上限只剩 {remaining} 次。"
                    "现在必须切换到最小数值交付模式。"
                    f"下一次 execute_code 必须优先写入或修复 {expected_result_file.name}，而不是继续探索模型、出图或打印检查。"
                    f"{minimum_delivery_contract}"
                    "禁止为了字体、图例位置、排版美观、重复打印检查或重复读取整张工作表而再次调用 execute_code。"
                    "如果已有图已经按 Chart language policy 成功生成，不要仅因字体警告或样式问题重新出图。"
                    f"若 {subtask_title} 仍有未完成子问题，只保留真正影响最终数值结论的必要计算。"
                ),
            }
        )

    async def _force_blocked_result(
        self,
        expected_result_file: Path,
        subtask_title: str,
        reason: str,
    ) -> CoderToWriter:
        blocked_id = f"{_sanitize_section_name(subtask_title)}_blocked"
        blocked_result = {
            "id": blocked_id,
            "name": f"{subtask_title}执行状态",
            "value": "blocked",
            "paper_value": "",
            "computed_value": "blocked",
            "unit": "",
            "formula": "",
            "inputs": {},
            "source_data": [],
            "code_cell": "",
            "evidence": reason,
            "source": "coder_guardrail",
            "verified": False,
            "status": "blocked",
            "warnings": [reason],
        }

        existing_payload: dict[str, object] = {}
        if expected_result_file.exists():
            try:
                existing_payload = json.loads(expected_result_file.read_text(encoding="utf-8"))
            except Exception:
                existing_payload = {}

        existing_key_results = existing_payload.get("key_results", [])
        if not isinstance(existing_key_results, list):
            existing_key_results = []

        filtered_key_results = [
            item for item in existing_key_results if isinstance(item, dict) and item.get("id") != blocked_id
        ]
        has_verified = any(item.get("status") == "verified" for item in filtered_key_results if isinstance(item, dict))
        if not has_verified:
            filtered_key_results.extend(
                _salvage_existing_payload_key_results(
                    existing_payload,
                    expected_result_file.name,
                    subtask_title,
                )
            )
            filtered_key_results.extend(
                _salvage_artifact_key_results(self.work_dir, subtask_title)
            )
            has_verified = any(item.get("status") == "verified" for item in filtered_key_results if isinstance(item, dict))

        # Only add the blocked sentinel when there are NO verified results.
        # When verified results exist, preserve them and add a warning instead.
        if has_verified:
            blocked_result = None  # type: ignore[assignment]
        else:
            filtered_key_results.append(blocked_result)

        existing_generated_files = existing_payload.get("generated_files", [])
        if not isinstance(existing_generated_files, list):
            existing_generated_files = []
        existing_generated_files = [
            item
            for item in existing_generated_files
            if not is_image_path(normalize_artifact_path(item.get("path") if isinstance(item, dict) else item))
        ]
        salvage_files = [
            item
            for item in _list_existing_artifacts(self.work_dir)
            if not is_image_path(normalize_artifact_path(item))
        ]
        existing_generated_files = [
            *existing_generated_files,
            *salvage_files,
        ]

        existing_warnings = existing_payload.get("warnings", [])
        if not isinstance(existing_warnings, list):
            existing_warnings = []

        existing_summary = str(existing_payload.get("summary", "") or "").strip()
        if has_verified:
            blocked_summary = f"{subtask_title} 部分完成：已有 verified 结果被保留，但因 {reason} 未能继续生成附加材料"
            # Sanitize warning text to avoid blocker keyword classification
            warning_reason = f"工具调用达到上限，已保留已验证结果，未继续生成附加材料"
        else:
            blocked_summary = f"{subtask_title} 被阻断：{reason}"
            warning_reason = reason
        if existing_summary and blocked_summary not in existing_summary:
            summary = f"{existing_summary}\n\n{blocked_summary}"
        else:
            summary = blocked_summary

        payload = {
            "section": existing_payload.get("section") or subtask_title,
            "summary": summary,
            "key_results": filtered_key_results,
            "generated_files": _dedupe_jsonish_items(existing_generated_files),
            "artifact_evidence": _dedupe_jsonish_items(_list_existing_image_evidence(self.work_dir)),
            "warnings": _dedupe_jsonish_items([*existing_warnings, warning_reason]),
        }
        expected_result_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=reason,
                type="warning",
                agent="coder",
            ),
        )
        # 被阻断时仍返回当前工作目录中已有的图片，避免丢失已生成的图表
        blocked_images = await self.code_interpreter.get_created_images(subtask_title) if self.code_interpreter else []
        if has_verified:
            response_text = f"{subtask_title} 部分完成：已有 verified 结果被保留，因 {reason} 未能继续生成附加材料"
        else:
            response_text = f"{subtask_title} 已阻断：{reason}"
        return CoderToWriter(
            coder_response=response_text,
            created_images=blocked_images,
            structured_result_files=[expected_result_file.name],
        )

    async def _handle_repeated_code(self, code: str, subtask_title: str) -> bool:
        """连续重复执行同一段代码时，强制模型收敛而不是无休止重跑。"""
        if code == self.last_executed_code:
            self.same_code_repeat_count += 1
        else:
            self.last_executed_code = code
            self.same_code_repeat_count = 1

        if self.same_code_repeat_count < 3:
            return False

        logger.warning("检测到相同代码被连续重复执行，要求模型直接收口输出")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="检测到代码手重复执行相同代码，已要求其停止重跑并直接总结结果",
                type="warning",
                agent="coder",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    f"你刚刚已经连续多次执行完全相同的代码。不要再次执行这段代码。"
                    f"请基于现有输出判断{subtask_title}是否已经完成："
                    "若已完成，直接输出最终结果摘要、关键结论、生成文件和后续建议；"
                    "若未完成，只允许提出下一步与当前代码不同的必要动作。"
                ),
            }
        )
        return True





    async def run(self, prompt: str, subtask_title: str) -> CoderToWriter:
        logger.info(f"{self.__class__.__name__}:开始:执行子任务: {subtask_title}")
        expected_chart_language = _chart_language_from_prompt(prompt)
        if self.code_interpreter:
            self.code_interpreter.add_section(subtask_title)
        self.current_chat_turns = 0

        # 如果是第一次运行，则添加系统提示
        if self.is_first_run:
            logger.info("首次运行，添加系统提示和数据集文件信息")
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
            # 当前数据集文件
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": f"当前文件夹下的数据集文件\n{get_current_files(self.work_dir, 'data')}",
                }
            )

        await self._ensure_structured_result_contract(subtask_title)

        # 添加 sub_task
        logger.info(f"添加子任务提示: {prompt}")
        await self.append_chat_history({"role": "user", "content": prompt})

        retry_count = 0
        total_retry_count = 0
        total_tool_calls = 0
        last_error_message = ""
        structured_result_retry_count = 0
        near_tool_budget_warned = False
        minimal_delivery_forced = False
        expected_result_file = Path(self.work_dir) / self._structured_result_filename(subtask_title)
        started_at = time.monotonic()

        while True:
            elapsed = time.monotonic() - started_at
            if elapsed >= self.max_wall_seconds:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大执行时间 {self.max_wall_seconds} 秒，任务被阻断",
                )

            if total_tool_calls >= self.max_total_tool_calls:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大工具调用次数 {self.max_total_tool_calls}，任务被阻断",
                )

            if total_retry_count >= self.max_retries:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大错误重试次数 {self.max_retries}，任务被阻断",
                )

            if retry_count >= self.max_retries:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"连续错误重试达到上限 {self.max_retries}，最后错误：{last_error_message}",
                )

            if self.current_chat_turns >= self.max_chat_turns:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"达到最大对话轮次 {self.max_chat_turns}，任务被阻断",
                )

            self.current_chat_turns += 1
            logger.info(f"当前对话轮次: {self.current_chat_turns}")

            try:
                response = await self.model.chat(
                    history=self.chat_history,
                    tools=coder_tools,
                    tool_choice="auto",
                    agent_name=self.__class__.__name__,
                    sub_title=subtask_title,
                )

                # 如果有工具调用
                if (
                    hasattr(response.choices[0].message, "tool_calls")
                    and response.choices[0].message.tool_calls
                ):
                    logger.info("检测到工具调用")
                    tool_call = response.choices[0].message.tool_calls[0]
                    tool_id = tool_call.id

                    if tool_call.function.name == "execute_code":
                        logger.info(f"调用工具: {tool_call.function.name}")

                        code = json.loads(tool_call.function.arguments)["code"]

                        # --- Pre-execution guards (run BEFORE counting tool calls) ---
                        # If a guard blocks, the call is rejected without consuming budget.
                        if await self._handle_completion_loop(
                            code,
                            subtask_title,
                            expected_result_file,
                            tool_id,
                        ):
                            continue

                        if await self._handle_repeated_code(code, subtask_title):
                            continue

                        chart_guard_message = _chart_language_guard_message(self.figure_mode, code, expected_chart_language)
                        if chart_guard_message:
                            await self.append_chat_history(
                                {
                                    "role": "user",
                                    "content": chart_guard_message,
                                }
                            )
                            await redis_manager.publish_message(
                                self.task_id,
                                SystemMessage(
                                    content=chart_guard_message,
                                    type="warning",
                                    agent="coder",
                                ),
                            )
                            continue

                        # --- All guards passed — now count and execute ---
                        total_tool_calls += 1
                        budget_ratio = total_tool_calls / max(self.max_total_tool_calls, 1)
                        if (
                            not near_tool_budget_warned
                            and budget_ratio >= 0.5
                        ):
                            near_tool_budget_warned = True
                            await self._warn_near_tool_budget(
                                total_tool_calls,
                                subtask_title,
                                expected_result_file,
                            )
                        if (
                            not minimal_delivery_forced
                            and budget_ratio >= 0.7
                        ):
                            minimal_delivery_forced = True
                            await self.append_chat_history(
                                {
                                    "role": "user",
                                    "content": (
                                        f"工具调用已用 {total_tool_calls}/{self.max_total_tool_calls} 次（{budget_ratio:.0%}）。"
                                        "已强制进入最小数值交付模式：\n"
                                        f"- 下一次 execute_code 只允许写入 {expected_result_file.name}。\n"
                                        "- 禁止读目录、查函数签名、画图、打印检查。\n"
                                        "- 禁止探索新算法或重新加载数据。\n"
                                        "- 只输出已确认的 key_results 和 generated_files。"
                                    ),
                                }
                            )
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=f"代码手调用{tool_call.function.name}工具",
                                agent="coder",
                            ),
                        )

                        # 更新对话历史 - 添加助手的响应
                        await self.append_chat_history(
                            response.choices[0].message.model_dump()
                        )
                        logger.info(response.choices[0].message.model_dump())

                        # 执行工具调用
                        logger.info("执行工具调用")
                        (
                            text_to_gpt,
                            error_occurred,
                            error_message,
                        ) = await self.code_interpreter.execute_code(code)

                        # 添加工具执行结果
                        if error_occurred:
                            await self.append_chat_history(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "name": "execute_code",
                                    "content": error_message,
                                }
                            )

                            logger.warning(f"代码执行错误: {error_message}")
                            retry_count += 1
                            total_retry_count += 1
                            logger.info(f"当前尝试次:{retry_count} / {self.max_retries}")
                            last_error_message = error_message
                            reflection_prompt = get_reflection_prompt(error_message, code)

                            await redis_manager.publish_message(
                                self.task_id,
                                SystemMessage(
                                    content="代码手反思纠正错误",
                                    type="error",
                                    agent="coder",
                                ),
                            )

                            await self.append_chat_history(
                                {"role": "user", "content": reflection_prompt}
                            )
                            continue
                        else:
                            await self.append_chat_history(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "name": "execute_code",
                                    "content": text_to_gpt,
                                }
                            )
                            await redis_manager.publish_message(
                                self.task_id,
                                InterpreterMessage(
                                    type="result",
                                    content=text_to_gpt,
                                    section=subtask_title,
                                ),
                            )
                            # 成功执行后继续循环，等待下一步指令
                            continue
                else:
                    # 没有工具调用，表示任务完成
                    logger.info("没有工具调用，任务完成")
                    is_valid, validation_error = _validate_structured_result_file(
                        expected_result_file,
                        subtask_title,
                    )
                    if not is_valid:
                        structured_result_retry_count += 1
                        await self.append_chat_history(
                            {
                                "role": "user",
                                "content": (
                                    f"你还不能结束，因为结构化结果文件校验失败：{validation_error}。"
                                    f"请立即通过执行代码生成或修复 {expected_result_file.name}，"
                                    "然后再给出最终总结。"
                                ),
                            }
                        )
                        if structured_result_retry_count >= 3:
                            return await self._force_blocked_result(
                                expected_result_file,
                                subtask_title,
                                f"未能生成有效的结构化结果文件: {validation_error}",
                            )
                        continue

                    await redis_manager.publish_message(
                        self.task_id,
                        InterpreterMessage(
                            type="result",
                            content=response.choices[0].message.content or "",
                            section=subtask_title,
                        ),
                    )
                    return CoderToWriter(
                        coder_response=response.choices[0].message.content,
                        created_images=await self.code_interpreter.get_created_images(
                            subtask_title
                        ),
                        structured_result_files=[expected_result_file.name],
                    )

            except Exception as e:
                logger.error(f"执行过程中发生异常: {str(e)}")
                retry_count += 1
                total_retry_count += 1
                last_error_message = str(e)
                continue
            logger.info(f"{self.__class__.__name__}:完成:执行子任务: {subtask_title}")
