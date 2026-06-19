"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.llm.llm import LLM
from app.guidance.contracts import (
    ApprovedModelSpec,
    GeneratedProgram,
    ModelReview,
    ModelSpec,
    ProblemSpec,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMProblemDecomposer:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def decompose(self, *, question: str, data_files: list[str], task: dict) -> ProblemSpec:
        return await _request_model(
            self.llm,
            ProblemSpec,
            """你是数学建模拆题专家。只输出 ProblemSpec JSON。拆出子问题、目标、对象、数据、约束、输出要求、题设事实、待确认信息、问题类型和候选方向。不得提出最终参数。""",
            {"question": question, "data_files": data_files},
            "GuidanceDecomposer",
        )


class LLMModeler:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def model(self, *, problem_spec: ProblemSpec, task: dict, work_dir: Path) -> ModelSpec:
        return await _request_model(
            self.llm,
            ModelSpec,
            """你是数学建模方案专家。只输出 ModelSpec JSON。比较候选方法并选择一个，完整给出假设、符号、公式、参数来源、目标/约束、求解步骤、必需结果和图片。若支路分解不可唯一识别，必须给出显式识别约束并披露条件性。required_outputs 至少包含 solver_results.json、parameter_registry.json、result_registry.json 和必要 figures/*.png。""",
            problem_spec.model_dump(mode="json"),
            "GuidanceModeler",
        )


class LLMModelReviewer:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def review(self, *, model_spec: ModelSpec, task: dict, work_dir: Path) -> ModelReview:
        return await _request_model(
            self.llm,
            ModelReview,
            """你是独立模型审核员。只输出 ModelReview JSON。严格检查可辨识性、维度/单位、数据充分性、约束与计算可行性。只有所有关键问题已由显式假设或约束解决时才 approved，否则 revision_required 或 blocked。""",
            model_spec.model_dump(mode="json"),
            "GuidanceModelReviewer",
        )


class LLMProgramGenerator:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def generate(
        self,
        *,
        approved_model: ApprovedModelSpec,
        task: dict,
        work_dir: Path,
    ) -> GeneratedProgram:
        data_files = sorted(
            path.relative_to(work_dir).as_posix()
            for path in (work_dir / "data").rglob("*")
            if path.is_file()
        ) if (work_dir / "data").is_dir() else []
        return await _request_model(
            self.llm,
            GeneratedProgram,
            """你是一次性求解程序生成器。只输出 GeneratedProgram JSON，其中 source_code 是完整可运行的 Python solve.py。程序必须直接读取给定数据文件，执行完整拟合/优化，写出所有 required_outputs；parameter_registry.json 记录参数值与来源，result_registry.json 记录已验证结果、残差指标与 source_data，图片保存到 figures/。禁止工具调用、网络、subprocess、eval/exec，也禁止运行期间补代码。""",
            {
                "approved_model": approved_model.model_dump(mode="json"),
                "data_files": data_files,
            },
            "GuidanceProgramGenerator",
        )


async def _request_model(
    llm: LLM,
    model_type: type[ModelT],
    system_prompt: str,
    payload: Any,
    agent_name: str,
) -> ModelT:
    response = await llm.chat(
        history=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        tools=None,
        tool_choice="none",
        agent_name=agent_name,
        sub_title=agent_name,
    )
    content = str(response.choices[0].message.content or "")
    return model_type.model_validate(_extract_json(content))


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload
