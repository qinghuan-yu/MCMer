"""
建模手 Agent - 为每个子问题设计数学建模方案
"""
from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import MODELER_PROMPT
from app.schemas.A2A import CoordinatorToModeler, ModelerToCoder
from app.utils.log_util import logger
import json
import re
from icecream import ic


def repair_json(json_str: str) -> dict | None:
    """尝试修复 LLM 输出的格式错误的 JSON"""
    json_str = json_str.replace("```json", "").replace("```", "").strip()

    # 尝试直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 修复字符串值内的未转义双引号
    try:
        fixed = re.sub(
            r'(?<=: ")(.*?)(?=",\s*\n\s*"|"\s*\n\s*})',
            lambda m: m.group(0).replace('"', '\\"'),
            json_str,
            flags=re.DOTALL,
        )
        return json.loads(fixed)
    except (json.JSONDecodeError, re.error):
        pass

    # 最后尝试用正则提取键值对
    try:
        pattern = r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.|"(?!,\s*\n)|"(?!\s*\n\s*}))*)"'
        matches = re.findall(pattern, json_str, re.DOTALL)
        if matches:
            return {k: v.replace('\\"', '"') for k, v in matches}
    except re.error:
        pass

    return None


class ModelerAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        max_chat_turns: int = 30,
    ) -> None:
        super().__init__(task_id, model, max_chat_turns)
        self.system_prompt = MODELER_PROMPT

    async def run(self, coordinator_to_modeler: CoordinatorToModeler) -> ModelerToCoder:
        await self.append_chat_history(
            {"role": "system", "content": self.system_prompt}
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": json.dumps(coordinator_to_modeler.questions, ensure_ascii=False, default=str),
            }
        )

        max_parse_retries = 3
        attempt = 0
        while True:
            response = await self.model.chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
            )

            json_str = response.choices[0].message.content
            if not json_str:
                raise ValueError("返回的 JSON 字符串为空，请检查输入内容。")

            questions_solution = repair_json(json_str)
            if questions_solution and isinstance(questions_solution, dict):
                # 检查是否包含建模方案的 key（排除纯元数据 key）
                valid_keys = {k for k in questions_solution if k not in ("sub_questions", "questions", "ques_count", "title", "background")}
                if valid_keys:
                    ic(questions_solution)
                    return ModelerToCoder(questions_solution=questions_solution)
                else:
                    logger.warning(f"JSON 缺少建模方案 key，收到的 key: {list(questions_solution.keys())}")

            attempt += 1
            logger.warning(
                f"JSON 解析失败 (第{attempt}次)，请求模型重新生成"
            )
            await self.append_chat_history(
                {"role": "assistant", "content": json_str}
            )
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": "你返回的JSON格式有误，请严格按照JSON格式重新输出，注意字符串值内的双引号必须转义为\\\"，不要包含未转义的特殊字符。",
                }
            )

            if attempt % max_parse_retries == 0:
                logger.warning("ModelerAgent 连续解析失败，已继续请求模型纠偏重试")
