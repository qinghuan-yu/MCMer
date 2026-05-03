"""
代码手 Agent - 执行代码、纠错、生成可视化
"""
from app.core.agents.agent import Agent
from app.config.setting import settings
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, InterpreterMessage
from app.tools.base_interpreter import BaseCodeInterpreter
from app.core.llm.llm import LLM
from app.schemas.A2A import CoderToWriter
from app.core.prompts import CODER_PROMPT, get_reflection_prompt
from app.utils.common_utils import get_current_files
from pathlib import Path
import importlib.metadata
import json
import re
from app.core.functions import coder_tools


class CoderAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        work_dir: str,
        max_chat_turns: int = settings.MAX_CHAT_TURNS,
        max_retries: int = settings.MAX_RETRIES,
        code_interpreter: BaseCodeInterpreter = None,
    ) -> None:
        super().__init__(task_id, model, max_chat_turns)
        self.work_dir = work_dir
        self.max_retries = max_retries
        self.is_first_run = True
        self.system_prompt = CODER_PROMPT
        self.code_interpreter = code_interpreter
        self.last_executed_code = ""
        self.same_code_repeat_count = 0

    @staticmethod
    def _sanitize_section_name(section: str) -> str:
        sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", (section or "result").strip())
        sanitized = sanitized.strip("_")
        return sanitized or "result"

    def _structured_result_filename(self, subtask_title: str) -> str:
        return f"{self._sanitize_section_name(subtask_title)}_structured_results.json"

    @staticmethod
    def _looks_like_non_mutating_summary_code(code: str) -> bool:
        normalized = (code or "").lower()
        if not normalized.strip():
            return False

        summary_markers = [
            "最终总结报告",
            "检查是否还有其他需要修复的问题",
            "所有检查通过",
            "任务已完成",
            "print(",
        ]
        has_summary_intent = any(marker.lower() in normalized for marker in summary_markers)
        if not has_summary_intent:
            return False

        write_markers = [
            "write_text(",
            "write_bytes(",
            "to_csv(",
            "to_excel(",
            "to_json(",
            "json.dump(",
            "savefig(",
            "document.save(",
            "open(",
        ]

        if "open(" in normalized:
            if any(mode in normalized for mode in ["'w'", '"w"', "'a'", '"a"', "'wb'", '"wb"']):
                return False

        return not any(marker in normalized for marker in write_markers if marker != "open(")

    async def _handle_completion_loop(
        self,
        code: str,
        subtask_title: str,
        expected_result_file: Path,
        tool_id: str,
    ) -> bool:
        is_valid, _ = self._validate_structured_result_file(expected_result_file, subtask_title)
        if not is_valid:
            return False

        if not self._looks_like_non_mutating_summary_code(code):
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

    @staticmethod
    def _installed_package_summary(limit: int = 80) -> str:
        preferred = [
            "numpy", "pandas", "scipy", "matplotlib", "seaborn", "openpyxl",
            "pypdf", "python-docx", "aiofiles", "fastapi", "pydantic",
        ]
        installed = {dist.metadata.get("Name", "") for dist in importlib.metadata.distributions()}
        ordered = [name for name in preferred if name in installed]
        remaining = sorted(name for name in installed if name and name not in ordered)
        package_names = ordered + remaining
        if len(package_names) > limit:
            package_names = package_names[:limit] + ["..."]
        return ", ".join(package_names)

    @staticmethod
    def _validate_structured_result_file(file_path: Path, expected_section: str) -> tuple[bool, str]:
        if not file_path.exists() or not file_path.is_file():
            return False, "结构化结果文件不存在"

        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"结构化结果文件不是有效 JSON: {exc}"

        if not isinstance(payload, dict):
            return False, "结构化结果文件顶层必须是 JSON 对象"

        required_keys = ["section", "summary", "key_results", "generated_files", "warnings"]
        missing = [key for key in required_keys if key not in payload]
        if missing:
            return False, f"结构化结果文件缺少字段: {', '.join(missing)}"

        if not isinstance(payload.get("key_results"), list):
            return False, "key_results 必须是数组"

        if payload.get("section") != expected_section:
            return False, f"section 字段必须为 {expected_section}"

        return True, ""

    async def _ensure_structured_result_contract(self, subtask_title: str) -> None:
        result_filename = self._structured_result_filename(subtask_title)
        installed_packages = self._installed_package_summary()
        contract_prompt = (
            f"当前子任务必须遵守以下执行契约：\n"
            f"1. 只能使用当前环境已安装的库；已知可用包示例：{installed_packages}\n"
            "2. 如果需要第三方库但环境未安装，必须立刻改用标准库、已安装库或已有结果文件，不能继续依赖不存在的包。\n"
            f"3. 在结束前，必须在工作目录写出结构化结果文件 {result_filename}。\n"
            "4. 该 JSON 文件必须包含字段：section、summary、key_results、generated_files、warnings。\n"
            f"5. section 字段必须严格等于 {subtask_title}。\n"
            "6. key_results 必须是数组；每个元素至少应包含 name、value、unit、evidence、source。\n"
            "7. 若结果不可靠，必须写入 warnings，并在 key_results 中显式标注无法确认。"
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
        last_error_message = ""
        structured_result_retry_count = 0
        expected_result_file = Path(self.work_dir) / self._structured_result_filename(subtask_title)

        while True:
            if retry_count >= self.max_retries:
                await self._soft_reset_retry_limit(last_error_message)
                retry_count = 0
                continue

            if self.current_chat_turns >= self.max_chat_turns:
                await self._soft_reset_chat_limit()
                self.current_chat_turns = 0
                continue

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
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=f"代码手调用{tool_call.function.name}工具",
                                agent="coder",
                            ),
                        )

                        code = json.loads(tool_call.function.arguments)["code"]

                        if await self._handle_completion_loop(
                            code,
                            subtask_title,
                            expected_result_file,
                            tool_id,
                        ):
                            continue

                        if await self._handle_repeated_code(code, subtask_title):
                            continue

                        await redis_manager.publish_message(
                            self.task_id,
                            InterpreterMessage(
                                type="code",
                                content=code,
                                section=subtask_title,
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
                    is_valid, validation_error = self._validate_structured_result_file(
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
                            raise RuntimeError(
                                f"{subtask_title} 未能按要求生成有效的结构化结果文件: {validation_error}"
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
                last_error_message = str(e)
                continue
            logger.info(f"{self.__class__.__name__}:完成:执行子任务: {subtask_title}")
