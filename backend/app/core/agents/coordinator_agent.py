"""
协调手 Agent - 分析题目，拆解子问题
"""
from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import COORDINATOR_PROMPT
import json
import re
from app.utils.log_util import logger
from app.schemas.A2A import CoordinatorToModeler, Questions, SubQuestion


class CoordinatorAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        max_chat_turns: int = 30,
    ) -> None:
        super().__init__(task_id, model, max_chat_turns)
        self.system_prompt = COORDINATOR_PROMPT

    async def run(self, ques_all: str) -> CoordinatorToModeler:
        """用户输入问题 使用LLM 格式化 questions"""
        await self.append_chat_history(
            {"role": "system", "content": self.system_prompt}
        )
        await self.append_chat_history({"role": "user", "content": ques_all})
        max_retries = 3
        attempt = 0
        while True:
            try:
                response = await self.model.chat(
                    history=self.chat_history,
                    agent_name=self.__class__.__name__,
                )
                json_str = response.choices[0].message.content

                # 清理 JSON 字符串
                json_str = json_str.replace("```json", "").replace("```", "").strip()
                json_str = re.sub(r"[\x00-\x1F\x7F]", "", json_str)

                if not json_str:
                    raise ValueError("返回的 JSON 字符串为空")

                questions_data = json.loads(json_str)
                logger.info(f"questions:{questions_data}")

                # 兼容多种 JSON 结构
                raw_questions = {}
                ques_count = 0

                if isinstance(questions_data, list):
                    # 模型直接返回问题列表 [{problem: ..., subproblems: [...]}, ...]
                    raw_questions = {"background": "", "sub_questions": []}
                    for i, item in enumerate(questions_data, start=1):
                        if isinstance(item, dict):
                            # 每个 problem 本身作为一个 sub_question
                            desc = item.get("description", "") or item.get("problem", "")
                            subproblems = item.get("subproblems", []) or item.get("sub_questions", [])
                            if subproblems:
                                for sp in subproblems:
                                    if isinstance(sp, dict):
                                        raw_questions["sub_questions"].append(sp)
                                    elif isinstance(sp, str):
                                        raw_questions["sub_questions"].append({"id": i, "title": f"子问题 {i}", "description": sp})
                            else:
                                raw_questions["sub_questions"].append({
                                    "id": i,
                                    "title": item.get("problem", f"问题 {i}"),
                                    "description": desc,
                                })
                    ques_count = len(raw_questions["sub_questions"])

                elif isinstance(questions_data, dict):
                    raw_questions = questions_data.get("questions", questions_data)
                    if isinstance(raw_questions, list):
                        raw_questions = {"background": "", "sub_questions": raw_questions}
                    # 兼容不同 key 名
                    sq_list = (
                        raw_questions.get("sub_questions")
                        or raw_questions.get("subproblems")
                        or raw_questions.get("items")
                        or []
                    )
                    ques_count = int(
                        questions_data.get("ques_count")
                        or len(sq_list)
                    )
                    raw_questions["sub_questions"] = sq_list

                else:
                    raise ValueError("协调手返回了无效结构")

                # 构建 Questions 对象
                sub_questions = []
                for i, sq in enumerate(raw_questions.get("sub_questions", []) or [], start=1):
                    if isinstance(sq, str):
                        sq = {
                            "id": i,
                            "title": f"子问题 {i}",
                            "description": sq,
                            "keywords": [],
                        }
                    if not isinstance(sq, dict):
                        continue
                    sub_questions.append(
                        SubQuestion(
                            id=int(sq.get("id", i)),
                            title=str(sq.get("title", f"子问题 {i}")),
                            description=str(sq.get("description", "")),
                            keywords=sq.get("keywords", []),
                        )
                    )

                questions = Questions(
                    background=str(raw_questions.get("background", "")),
                    sub_questions=sub_questions,
                )

                logger.info(f"题目拆解完成: {ques_count} 个子问题")
                return CoordinatorToModeler(questions=questions, ques_count=ques_count)

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                attempt += 1
                logger.warning(f"解析失败 (尝试 {attempt}/{max_retries}): {str(e)}")

                # 添加错误反馈提示
                error_prompt = f"⚠️ 上次响应格式错误: {str(e)}。请严格输出JSON格式"
                await self.append_chat_history({
                    "role": "system",
                    "content": self.system_prompt + "\n" + error_prompt
                })

                if attempt % max_retries == 0:
                    logger.warning("CoordinatorAgent 连续解析失败，已继续请求模型纠偏重试")
