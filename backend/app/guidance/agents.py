"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from app.core.llm.llm import LLM
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import (
    ModelReview,
    ModelSpec,
    ProblemSpec,
    RevisedModelSpec,
)


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
    ) -> ModelSpec:
        return await _request_model(
            llm=self.llm,
            model_type=ModelSpec,
            system_prompt="""你是数学建模方案专家。只输出 ModelSpec JSON。比较候选方法并选择一个。data_profile 是已读取上传文件后生成的权威数据画像：其中列出的文件、工作表、列名、有效列数和样例行均视为实际存在，禁止把这些表误判为缺失；source_requirements 中 status=missing 的来源才属于确定性缺失。subproblem_models 必须逐一覆盖 problem_spec 的全部子问题；输出的 subproblem_id 必须精确使用各输入子问题的 id 字段（如 problem1），不得使用仅供阅读的 subproblem_id 题面编号（如 1.1）。每项必须明确 execution_status。数据与题设足够时使用 solvable，完整给出 objective、selected_method、rationale、equations、parameters、constraints、solve_steps、expected_results；若目标要求不同直径、工况或类别各自给出参数，必须分别估计并逐组输出，禁止只给全局平均参数。题面引用但上传文件中不存在的附录、参数表或观测属于缺失输入，必须使用 blocked，只给 objective、blocking_reason、missing_inputs、recovery_action、expected_results，禁止虚构公式、参数来源或数值把它包装成 solvable。依赖 blocked 子问题结果、缺失物理关系或缺失约束的下游子问题也必须标为 blocked，假设和正则化不能替代缺失信息。允许审核通过一个 coverage_status=partial 的诚实执行方案。输出必须简洁：每个可解子问题最多 8 个核心参数；复杂模型必须用参数组（如 theta_problem4）概括，不要枚举所有标量；长推导留给 solve.py 和 parameter_registry。每个 parameter 必须给出 parameter_id、symbol、meaning、unit、source_type、source_detail、estimation_method 和非空 constraints。每个 solvable 子问题都必须在 constraints、solve_steps 或 parameter.constraints 中写明“可辨识性计划”，并至少包含 rank/Jacobian/设计矩阵满秩/唯一性检查/固定参数/额外识别约束之一；若不可唯一识别，必须给出显式识别约束并披露条件性。执行文件清单由系统管理，不得输出 required_outputs。图片 role 只允许 data_overview、model_explanation、result_fit、verification_diagnostic，并逐图声明 path、source_data 与 linked_result_ids。""",
            payload={
                **_compact_problem_spec_for_modeling(problem_spec),
                "data_profile": data_profile or {"files": []},
            },
            agent_name="GuidanceModeler",
            artifact_dir=work_dir,
            semantic_validator=_validate_model_identifiability_plan,
        )

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model: ModelSpec,
        review: ModelReview,
        data_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> RevisedModelSpec:
        return await _request_model(
            llm=self.llm,
            model_type=RevisedModelSpec,
            system_prompt="""你是数学建模方案修订专家。只输出完整 RevisedModelSpec JSON。严格根据独立审核的每一条 required_revisions 修订旧模型，并在 review_resolutions 中逐条原文填写 requirement，同时说明 resolution、affected_subproblem_ids 和 changed_fields；不得遗漏或添加无关解决项。subproblem_models 必须逐一覆盖全部子问题，subproblem_id 必须精确使用 problem_spec 中各子问题的 id 字段，不得使用题面编号，并明确 execution_status：solvable 项保留非空 equations、parameters、constraints、solve_steps、expected_results；缺少题面所引用附录、参数表或观测的子题必须改为 blocked，只登记 blocking_reason、missing_inputs、recovery_action 和 expected_results，禁止用假设补成伪可解模型；依赖 blocked 子问题结果或缺失物理关系的下游子题也必须 blocked。每个可解子问题最多 8 个核心参数，复杂模型用参数组概括，不要枚举所有标量；每个 solvable 项都必须在子问题约束、求解步骤或参数约束中写明“可辨识性计划”，并至少包含 rank/Jacobian/设计矩阵满秩/唯一性检查/固定参数/额外识别约束之一；每个参数必须保留来源和估计方法。不得回避审核项，也不得虚构数据。执行文件清单由系统管理，不得输出 required_outputs。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "previous_model": previous_model.model_dump(mode="json"),
                "model_review": review.model_dump(mode="json"),
                "data_profile": data_profile or {"files": []},
            },
            agent_name="GuidanceModelReviser",
            artifact_dir=work_dir,
            semantic_validator=_validate_model_identifiability_plan,
        )


class LLMModelReviewer:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
        model_spec: ModelSpec,
        task: dict,
        work_dir: Path,
    ) -> ModelReview:
        return await _request_model(
            llm=self.llm,
            model_type=ModelReview,
            system_prompt="""你是独立模型审核员。只输出 ModelReview JSON。严格检查可辨识性、维度/单位、数据充分性、约束与计算可行性。审核对象是执行方案而不是强迫每题都产出数值：若可解子题契约完整，缺失输入的子题已诚实标记 blocked 且给出恢复动作，可以 approved 一个 coverage_status=partial 的方案；若把缺失附录、参数表或观测虚构成 given/assumption 后继续求解，必须 revision_required 或 blocked。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "model_spec": model_spec.model_dump(mode="json"),
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


def _validate_model_identifiability_plan(model_spec: ModelSpec) -> None:
    keywords = (
        "identifiability",
        "identifiable",
        "unique",
        "rank",
        "jacobian",
        "fixed",
        "可辨识",
        "唯一",
        "秩",
        "雅可比",
        "固定",
        "额外约束",
        "识别约束",
    )
    missing: list[str] = []
    for subproblem in model_spec.subproblem_models:
        if subproblem.execution_status == "blocked":
            continue
        parameter_constraints = [
            constraint
            for parameter in subproblem.parameters
            for constraint in parameter.constraints
        ]
        text = " ".join(
            [*subproblem.constraints, *subproblem.solve_steps, *parameter_constraints]
        ).lower()
        if not any(keyword.lower() in text for keyword in keywords):
            missing.append(subproblem.subproblem_id)
    if missing:
        raise ValueError(
            "identifiability plan missing for subproblems: "
            + ", ".join(missing)
            + "; add explicit rank/Jacobian/uniqueness/fixed-parameter or extra-constraint checks."
        )


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
