"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

from pathlib import Path

from app.core.llm.llm import LLM
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import (
    ApprovedModelSpec,
    GeneratedProgram,
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
            system_prompt="""你是数学建模拆题专家。只输出 ProblemSpec JSON。拆出子问题、目标、对象、数据、约束、输出要求、题设事实、待确认信息、问题类型和候选方向。不得提出最终参数。""",
            agent_name="GuidanceDecomposer",
            artifact_dir=_task_artifact_dir(task),
            enrich_payload=lambda payload: {**payload, "task_summary": question},
        )
        return await requester.request({"question": question, "data_files": data_files})


class LLMModeler:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def model(self, *, problem_spec: ProblemSpec, task: dict, work_dir: Path) -> ModelSpec:
        return await _request_model(
            llm=self.llm,
            model_type=ModelSpec,
            system_prompt="""你是数学建模方案专家。只输出 ModelSpec JSON。比较候选方法并选择一个。subproblem_models 必须逐一覆盖 problem_spec 的全部子问题；每项完整给出 objective、selected_method、rationale、equations、parameters、constraints、solve_steps、expected_results。每个 parameter 必须给出 parameter_id、symbol、meaning、unit、source_type、source_detail、estimation_method 和非空 constraints。若不可唯一识别，必须给出显式识别约束并披露条件性。执行文件清单由系统管理，不得输出 required_outputs。图片 role 只允许 data_overview、model_explanation、result_fit、verification_diagnostic，并逐图声明 path、source_data 与 linked_result_ids。""",
            payload=problem_spec.model_dump(mode="json"),
            agent_name="GuidanceModeler",
            artifact_dir=work_dir,
        )

    async def revise(
        self,
        *,
        problem_spec: ProblemSpec,
        previous_model: ModelSpec,
        review: ModelReview,
        task: dict,
        work_dir: Path,
    ) -> RevisedModelSpec:
        return await _request_model(
            llm=self.llm,
            model_type=RevisedModelSpec,
            system_prompt="""你是数学建模方案修订专家。只输出完整 RevisedModelSpec JSON。严格根据独立审核的每一条 required_revisions 修订旧模型，并在 review_resolutions 中逐条原文填写 requirement，同时说明 resolution、affected_subproblem_ids 和 changed_fields；不得遗漏或添加无关解决项。subproblem_models 必须逐一覆盖全部子问题，并为每项保留非空 equations、parameters、constraints、solve_steps、expected_results；每个参数必须保留来源和估计方法。不得回避审核项，也不得虚构数据。执行文件清单由系统管理，不得输出 required_outputs。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "previous_model": previous_model.model_dump(mode="json"),
                "model_review": review.model_dump(mode="json"),
            },
            agent_name="GuidanceModelReviser",
            artifact_dir=work_dir,
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
            system_prompt="""你是独立模型审核员。只输出 ModelReview JSON。严格检查可辨识性、维度/单位、数据充分性、约束与计算可行性。只有所有关键问题已由显式假设或约束解决时才 approved，否则 revision_required 或 blocked。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "model_spec": model_spec.model_dump(mode="json"),
            },
            agent_name="GuidanceModelReviewer",
            artifact_dir=work_dir,
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
            llm=self.llm,
            model_type=GeneratedProgram,
            system_prompt="""你是一次性求解程序生成器。只输出 GeneratedProgram JSON，其中 source_code 是完整可运行的 Python solve.py，declared_outputs 必须按原顺序逐项等于 approved_model.required_outputs。程序必须直接读取给定数据文件，执行完整拟合或优化并写出全部 declared_outputs。四类 JSON 必须严格遵守以下协议且不得增加字段：parameter_registry.json={parameters:[{id,symbol,name,value,unit,source_type,source_ref,trust_status}],summary?}；result_registry.json={verified_results:[{id,result_type,claim_text,metrics:{数值指标},source_data:[路径]}],blocked_results:[{id,message,reason,source_data}]?,summary?}；figure_registry.json={figures:[{path,role,source_data:[路径],linked_result_ids:[结果ID]}]}，role 只能是 data_overview/model_explanation/result_fit/verification_diagnostic；solver_results.json={evidence:[...]}, 每项必须有 kind、subproblem_id、result_id。回归 evidence 使用 kind=regression、observed、predicted、residuals、constraint_values、parameter_stability={method,max_relative_change,threshold}；优化 evidence 使用 kind=optimization、objective_value、baseline_objective、constraints=[{name,value,lower_bound或upper_bound}]、sensitivity={method,max_relative_change,threshold}。solver evidence 的 result_id 必须与 verified_results 一一对应。禁止工具调用、网络、subprocess、eval/exec，也禁止运行期间补代码。""",
            payload={
                "approved_model": approved_model.model_dump(mode="json"),
                "data_files": data_files,
            },
            agent_name="GuidanceProgramGenerator",
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
):
    requester = StructuredContractRequester(
        llm=llm,
        model_type=model_type,
        system_prompt=system_prompt,
        agent_name=agent_name,
        artifact_dir=artifact_dir,
    )
    return await requester.request(payload)


def _task_artifact_dir(task: dict) -> Path | None:
    work_dir = task.get("work_dir")
    return Path(work_dir) if work_dir else None
