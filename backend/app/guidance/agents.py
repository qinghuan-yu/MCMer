"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from app.core.llm.llm import LLM
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import (
    ProblemSpec,
)
from app.guidance.model_ir import ModelIR, ModelIRReview
from app.guidance.model_ir_semantics import ModelIRSemanticValidator


class LLMProblemDecomposer:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def decompose(self, *, question: str, data_files: list[str], task: dict) -> ProblemSpec:
        requester = StructuredContractRequester(
            llm=self.llm,
            model_type=ProblemSpec,
            system_prompt="""你是数学建模拆题专家。只输出 ProblemSpec JSON。拆出子问题、目标、对象、数据、约束、输出要求、题设事实、待确认信息、问题类型和候选方向。必须对照 data_files 区分题面引用来源与实际上传文件；未上传的附录、参数表或观测不得写入 locked_facts。不得提出最终参数。""",
            agent_name="GuidanceDecomposer",
            artifact_dir=_task_artifact_dir(task),
            enrich_payload=lambda payload: {**payload, "task_summary": question},
        )
        return await requester.request({"question": question, "data_files": data_files})


class LLMModeler:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def model(
        self,
        *,
        problem_spec: ProblemSpec,
        data_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIR:
        return await _request_model(
            llm=self.llm,
            model_type=ModelIR,
            system_prompt="""你是数学建模 ModelIR 构建器。只输出符合目标 Schema 的 ModelIR JSON。逐一覆盖 problem_spec 的全部子问题，subproblem_id 必须精确使用输入 id。可解子问题必须用强类型表达式 AST 描述方程和约束，声明变量、数据引用、通用算子调用、推荐模型与简单基线、可辨识性/证据/稳健性验证以及证据绑定的最终答案；禁止用 free-text equations 作为数学语义，禁止输出 Python、solve.py 或其他隐式执行代码。operator_id 只能表示可复用数学能力，禁止引用题名或题号。data_profile 中列出的文件、工作表和列视为实际存在；source_requirements 中 status=missing 的来源才是确定缺失。数据、物理关系、约束或算子不足时必须输出 blocked，明确 blocking_reason、missing_inputs、recovery_action 和 expected_outputs；禁止用假设、经验常数或正则化伪造可解性。""",
            payload={
                **_compact_problem_spec_for_modeling(problem_spec),
                "data_profile": data_profile or {"files": []},
            },
            agent_name="GuidanceModeler",
            artifact_dir=work_dir,
            semantic_validator=_validate_model_ir_semantics,
        )

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model_ir: ModelIR,
        review: ModelIRReview,
        data_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIR:
        return await _request_model(
            llm=self.llm,
            model_type=ModelIR,
            system_prompt="""你是数学建模 ModelIR 修订器。只输出完整 ModelIR JSON。逐条处理 model_review.required_revisions，并保持所有子问题覆盖。可解项只能使用强类型表达式 AST 和通用算子调用，禁止 free-text equations、Python 或隐式执行代码；无法在现有数据和算子能力下诚实修复的子问题必须改为 blocked。不得通过虚构数据、参数或约束绕过审核。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "previous_model_ir": previous_model_ir.model_dump(mode="json"),
                "model_review": review.model_dump(mode="json"),
                "data_profile": data_profile or {"files": []},
            },
            agent_name="GuidanceModelReviser",
            artifact_dir=work_dir,
            semantic_validator=_validate_model_ir_semantics,
        )


class LLMModelReviewer:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
        model_ir: ModelIR,
        task: dict,
        work_dir: Path,
    ) -> ModelIRReview:
        return await _request_model(
            llm=self.llm,
            model_type=ModelIRReview,
            system_prompt="""你是独立 ModelIR 审核员。只输出 ModelIRReview JSON。严格审核强类型表达式、可辨识性、维度/单位、数据证据、算子前提、约束和计算可行性。不得把程序可运行等同于模型充分。诚实 blocked 的子问题可以保留；若 IR 虚构输入、缺少基线或证据绑定、依赖自由文本公式或隐式代码，必须 revision_required 或 blocked。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "model_ir": model_ir.model_dump(mode="json"),
            },
            agent_name="GuidanceModelReviewer",
            artifact_dir=work_dir,
        )


async def _request_model(
    *,
    llm: LLM,
    model_type,
    system_prompt: str,
    payload,
    agent_name: str,
    artifact_dir: Path,
    semantic_validator=None,
):
    requester = StructuredContractRequester(
        llm=llm,
        model_type=model_type,
        system_prompt=system_prompt,
        agent_name=agent_name,
        artifact_dir=artifact_dir,
        semantic_validator=semantic_validator,
    )
    return await requester.request(payload)


def _task_artifact_dir(task: dict) -> Path | None:
    work_dir = task.get("work_dir")
    return Path(work_dir) if work_dir else None


def _compact_problem_spec_for_modeling(problem_spec: ProblemSpec) -> dict[str, Any]:
    payload = problem_spec.model_dump(mode="json")
    return {
        "schema_version": payload.get("schema_version", "1.0"),
        "task_summary": _compact_task_summary(str(payload.get("task_summary") or "")),
        "subproblems": [
            _compact_subproblem(item)
            for item in payload.get("subproblems", [])
            if isinstance(item, dict)
        ],
        "data_sources": [
            _compact_mapping(item, ("name", "description", "tables", "path", "columns"))
            for item in payload.get("data_sources", [])
            if isinstance(item, dict)
        ],
        "source_requirements": payload.get("source_requirements", []),
        "locked_facts": _compact_list(payload.get("locked_facts", []), limit=12),
        "uncertainties": _compact_list(payload.get("uncertainties", []), limit=12),
    }


def _validate_model_ir_semantics(model_ir: ModelIR) -> None:
    diagnostics = ModelIRSemanticValidator().validate(model_ir)
    if diagnostics:
        details = "; ".join(
            f"{item.code} at {item.path}: {item.message}" for item in diagnostics
        )
        raise ValueError(f"Model IR semantic validation failed: {details}")


def _compact_subproblem(item: dict[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        item,
        (
            "id",
            "subproblem_id",
            "objective",
            "description",
            "goals",
            "inputs",
            "outputs",
            "constraints",
            "assumptions",
            "candidate_directions",
            "problem_type",
        ),
    )


def _compact_mapping(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in keys:
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, str):
            output[key] = _truncate_text(value, 240)
        elif isinstance(value, list):
            output[key] = _compact_list(value, limit=8)
        elif isinstance(value, dict):
            output[key] = {
                str(child_key): _truncate_text(str(child_value), 160)
                for child_key, child_value in list(value.items())[:8]
            }
        else:
            output[key] = value
    return output


def _compact_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    output: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, str):
            output.append(_truncate_text(item, 180))
        elif isinstance(item, dict):
            output.append(_compact_mapping(item, tuple(str(key) for key in item.keys())))
        else:
            output.append(item)
    return output


def _compact_task_summary(text: str) -> str:
    clean = " ".join(text.split())
    if len(clean) <= 300:
        return clean
    return (
        "Full task statement omitted from GuidanceModeler payload; "
        "use saved problem_spec.json and compact subproblem contracts."
    )


def _truncate_text(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + "...[truncated]"
