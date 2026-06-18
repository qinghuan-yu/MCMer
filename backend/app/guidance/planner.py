"""Single-call structured planner for the compute-first guidance pipeline."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from app.core.llm.llm import LLM
from app.services.config_manager import config_manager


class GuidancePlanner(Protocol):
    async def plan(
        self,
        *,
        question: str,
        data_files: list[str],
        workflow_mode: str,
    ) -> dict[str, Any]: ...


class LLMGuidancePlanner:
    """Produce one deterministic-compute specification without tool calls."""

    def __init__(self, model: LLM | None = None) -> None:
        self.model = model or LLM(
            model=config_manager.get_effective_model("MODELER_MODEL"),
            temperature=0.1,
            max_tokens=12288,
        )

    async def plan(
        self,
        *,
        question: str,
        data_files: list[str],
        workflow_mode: str,
    ) -> dict[str, Any]:
        response = await self.model.chat(
            history=[
                {
                    "role": "system",
                    "content": _PLANNER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f"题目：\n{question}\n\n"
                        f"可用文件：{json.dumps(data_files, ensure_ascii=False)}\n"
                        f"执行模式：{workflow_mode}\n\n"
                        "只返回一个 JSON 对象。"
                    ),
                },
            ],
            tools=None,
            tool_choice="none",
            agent_name="GuidancePlanner",
            sub_title="结构化计算规划",
        )
        content = str(response.choices[0].message.content or "")
        return normalize_solve_spec(_extract_json(content), question=question)


def normalize_solve_spec(payload: Any, *, question: str) -> dict[str, Any]:
    """Return a runner-compatible spec or an explicit blocked spec."""
    if not isinstance(payload, dict):
        return blocked_solve_spec(question, "planner did not return a JSON object")

    parameters = payload.get("parameters")
    results = payload.get("results")
    if not isinstance(parameters, list) or not parameters:
        return blocked_solve_spec(question, "planner did not register computable parameters")
    if not isinstance(results, list):
        payload["results"] = []

    payload.setdefault("spec_id", "guidance_solve_spec")
    payload.setdefault("subproblem_id", "all")
    payload.setdefault("problem_summary", "")
    payload.setdefault("method_guidance", "")
    payload.setdefault("assumptions", [])
    payload.setdefault("steps", [])
    return payload


def blocked_solve_spec(question: str, reason: str) -> dict[str, Any]:
    return {
        "spec_id": "guidance_blocked_spec",
        "subproblem_id": "all",
        "problem_summary": "当前题目尚未形成可执行的确定性计算规格。",
        "method_guidance": "保留题目与输入材料，补充参数定义后重新规划。",
        "assumptions": [],
        "steps": ["补充可计算的参数、公式与来源。"],
        "parameters": [
            {
                "symbol": "unresolved_model",
                "name": "待确定模型",
                "identifiability": "non_identifiable",
                "reason": reason,
                "next_required_data": [question[:200]],
            }
        ],
        "results": [],
    }


def _extract_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


_PLANNER_SYSTEM_PROMPT = """
你是高可信建模指导方案的结构化规划器。你不执行代码，也不生成论文。
你的唯一输出是供本地确定性计算器执行的 JSON。

顶层字段必须包含：spec_id、subproblem_id、problem_summary、method_guidance、
assumptions、steps、parameters、results。

parameters 中每项使用 symbol、name、unit、source_type、source_ref、source_location。
题设或上传材料中的常量直接写 value；派生参数写只含四则运算、乘方和已登记 symbol 的 formula。
无法从现有材料识别的参数必须写 identifiability=non_identifiable、reason 和 next_required_data，禁止猜值。

results 中每项使用 id、claim_text、parameters、source_data。claim_text 只能描述由登记参数直接支持的结论。
当前计算器只支持安全算术表达式。回归、优化、微分方程、仿真或文件列运算若不能展开为已登记参数公式，必须标记阻断，不能伪造结果。
""".strip()

