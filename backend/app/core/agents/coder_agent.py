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
import json
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

        # 添加 sub_task
        logger.info(f"添加子任务提示: {prompt}")
        await self.append_chat_history({"role": "user", "content": prompt})

        retry_count = 0
        last_error_message = ""

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
                    )

            except Exception as e:
                logger.error(f"执行过程中发生异常: {str(e)}")
                retry_count += 1
                last_error_message = str(e)
                continue
            logger.info(f"{self.__class__.__name__}:完成:执行子任务: {subtask_title}")
