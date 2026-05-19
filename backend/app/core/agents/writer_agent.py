"""
写作手 Agent - 生成完整论文
"""
from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import get_writer_prompt
from app.schemas.enums import CompTemplate, FormatOutPut
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, WriterMessage
import json
import re
from app.core.functions import writer_tools
from icecream import ic
from app.schemas.A2A import WriterResponse


_TOOL_PROTOCOL_RE = re.compile(
    r"(<\s*[｜|]+\s*DSML\s*[｜|]+|</\s*[｜|]+\s*DSML\s*[｜|]+|"
    r"\btool_calls\b|invoke\s+name\s*=|parameter\s+name\s*=|run_script)",
    re.IGNORECASE,
)


def _contains_tool_protocol_text(content: str) -> bool:
    """Return True when model/tool-call protocol text leaked into prose."""
    if not content:
        return False
    matches = _TOOL_PROTOCOL_RE.findall(content)
    if any("DSML" in str(match).upper() for match in matches):
        return True
    lowered = content.lower()
    return (
        "tool_calls" in lowered
        and ("invoke name" in lowered or "parameter name" in lowered or "run_script" in lowered)
    )


class WriterAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        max_chat_turns: int = 10,
        comp_template: CompTemplate = CompTemplate.CUMCM,
        format_output: FormatOutPut = FormatOutPut.Markdown,
        scholar: OpenAlexScholar = None,
        max_memory: int = 25,
    ) -> None:
        super().__init__(task_id, model, max_chat_turns, max_memory)
        self.format_out_put = format_output
        self.comp_template = comp_template
        self.scholar = scholar
        self.is_first_run = True
        self.system_prompt = get_writer_prompt(format_output)
        self.available_images: list[str] = []

    async def _run_search_papers_tool(self, tool_call, sub_title: str) -> None:
        tool_id = tool_call.id
        query = json.loads(tool_call.function.arguments)["query"]

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"写作手调用{tool_call.function.name}工具",
                agent="writer",
            ),
        )

        await redis_manager.publish_message(
            self.task_id,
            WriterMessage(
                type="text",
                content=f"搜索文献: {query}",
                section=sub_title,
            ),
        )

        try:
            papers = await self.scholar.search_papers(query)
        except Exception as e:
            error_msg = f"搜索文献失败: {str(e)}"
            logger.error(error_msg)
            await self.append_chat_history(
                {
                    "role": "tool",
                    "content": error_msg,
                    "tool_call_id": tool_id,
                    "name": "search_papers",
                }
            )
            return

        papers_str = self.scholar.papers_to_str(papers)
        logger.info(f"搜索文献结果\n{papers_str}")
        await self.append_chat_history(
            {
                "role": "tool",
                "content": papers_str,
                "tool_call_id": tool_id,
                "name": "search_papers",
            }
        )

    async def run(
        self,
        prompt: str,
        available_images: list[str] = None,
        sub_title: str = "",
    ) -> WriterResponse:
        """
        执行写作任务
        Args:
            prompt: 写作提示
            available_images: 可用的图片相对路径列表
            sub_title: 子任务标题
        """
        logger.info(f"subtitle是:{sub_title}")

        if self.is_first_run:
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )

        if available_images:
            self.available_images = available_images
            image_lines = "\n".join(
                [f"- ![{img}]({img})" for img in available_images]
            )
            image_prompt = (
                f"\n\n【必须插入的图片列表】\n"
                f"以下图片是代码手生成的，你必须在论文相关段落后用 Markdown 格式逐一插入：\n"
                f"{image_lines}\n"
                f"插入格式为独占一行的 ![描述](文件名)，每张图片后需配3行以上的分析解读。\n"
            )
            logger.info(f"image_prompt是:{image_prompt}")
            prompt = prompt + image_prompt

        logger.info(f"{self.__class__.__name__}:开始:执行对话")
        self.current_chat_turns += 1

        await self.append_chat_history({"role": "user", "content": prompt})

        footnotes = []

        response_content = ""
        tool_only_turns = 0
        force_plaintext_response = False
        for _ in range(self.max_chat_turns):
            response = await self.model.chat(
                history=self.chat_history,
                tools=None if force_plaintext_response else writer_tools,
                tool_choice="none" if force_plaintext_response else "auto",
                agent_name=self.__class__.__name__,
                sub_title=sub_title,
            )
            message = response.choices[0].message

            if hasattr(message, "tool_calls") and message.tool_calls:
                tool_only_turns += 1
                logger.info("检测到工具调用")
                await self.append_chat_history(message.model_dump())
                ic(message.model_dump())
                for tool_call in message.tool_calls:
                    if tool_call.function.name == "search_papers":
                        logger.info("调用工具: search_papers")
                        await self._run_search_papers_tool(tool_call, sub_title)

                if tool_only_turns >= 3:
                    force_plaintext_response = True
                    await self.append_chat_history(
                        {
                            "role": "user",
                            "content": (
                                "你已经完成必要的文献检索。"
                                "从现在开始禁止继续调用 search_papers。"
                                "请直接基于现有题目、结果文件、复核结论和已检索到的文献，输出完整论文正文 Markdown。"
                                "如果参考文献仍不足，可以先不写参考文献章节，但不能返回空内容。"
                            ),
                        }
                    )
                continue

            response_content = (message.content or "").strip()
            if _contains_tool_protocol_text(response_content):
                logger.warning(
                    "WriterAgent returned tool-call protocol text instead of Markdown; rejecting response."
                )
                response_content = ""
                force_plaintext_response = True
                await self.append_chat_history(
                    {
                        "role": "user",
                        "content": (
                            "你刚才输出了工具调用协议文本（例如 DSML/tool_calls/run_script），这不是论文正文。"
                            "禁止输出任何工具调用、XML/DSML 标签、run_script 内容或调试脚本。"
                            "请立即改为只输出完整 Markdown 论文正文。"
                        ),
                    }
                )
                continue
            if response_content:
                break

            force_plaintext_response = True

            await self.append_chat_history(
                {
                    "role": "user",
                    "content": (
                        "你上一轮返回了空内容。"
                        "请直接输出完整正文，不要只返回空字符串，也不要省略正文。"
                    ),
                }
            )

        if not response_content:
            raise RuntimeError(f"{sub_title or '写作任务'} 返回空内容")

        await redis_manager.publish_message(
            self.task_id,
            WriterMessage(
                type="section_complete",
                content=response_content or "",
                section=sub_title,
            ),
        )
        self.chat_history.append({"role": "assistant", "content": response_content})
        logger.info(f"{self.__class__.__name__}:完成:执行对话")
        return WriterResponse(response_content=response_content, footnotes=footnotes)

    async def summarize(self) -> str:
        """
        总结对话内容
        """
        try:
            await self.append_chat_history(
                {"role": "user", "content": "请简单总结以上完成什么任务取得什么结果:"}
            )
            response = await self.model.chat(
                history=self.chat_history, agent_name=self.__class__.__name__
            )
            await self.append_chat_history(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"总结生成失败: {str(e)}")
            return "由于网络原因无法生成详细总结，但已完成主要任务处理。"
