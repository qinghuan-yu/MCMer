"""Single-call LLM ports for the six-stage guidance pipeline."""

from __future__ import annotations

from typing import Any
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
from app.guidance.execution import validate_generated_program_source
from app.guidance.traffic_flow_template import build_wuyi_traffic_flow_program


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
            system_prompt="""你是数学建模方案专家。只输出 ModelSpec JSON。比较候选方法并选择一个。subproblem_models 必须逐一覆盖 problem_spec 的全部子问题；每项完整给出 objective、selected_method、rationale、equations、parameters、constraints、solve_steps、expected_results。输出必须简洁：每个子问题最多 8 个核心参数；复杂模型必须用参数组（如 theta_problem4）概括，不要枚举所有标量；长推导留给 solve.py 和 parameter_registry。每个 parameter 必须给出 parameter_id、symbol、meaning、unit、source_type、source_detail、estimation_method 和非空 constraints。每个子问题都必须在 constraints 或 solve_steps 中写明“可辨识性计划”，并至少包含 rank/Jacobian/设计矩阵满秩/唯一性检查/固定参数/额外识别约束之一；若不可唯一识别，必须给出显式识别约束并披露条件性。执行文件清单由系统管理，不得输出 required_outputs。图片 role 只允许 data_overview、model_explanation、result_fit、verification_diagnostic，并逐图声明 path、source_data 与 linked_result_ids。""",
            payload=_compact_problem_spec_for_modeling(problem_spec),
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
        task: dict,
        work_dir: Path,
    ) -> RevisedModelSpec:
        return await _request_model(
            llm=self.llm,
            model_type=RevisedModelSpec,
            system_prompt="""你是数学建模方案修订专家。只输出完整 RevisedModelSpec JSON。严格根据独立审核的每一条 required_revisions 修订旧模型，并在 review_resolutions 中逐条原文填写 requirement，同时说明 resolution、affected_subproblem_ids 和 changed_fields；不得遗漏或添加无关解决项。subproblem_models 必须逐一覆盖全部子问题，并为每项保留非空 equations、parameters、constraints、solve_steps、expected_results；每个子问题最多 8 个核心参数，复杂模型用参数组概括，不要枚举所有标量；每项都必须写明“可辨识性计划”，并至少包含 rank/Jacobian/设计矩阵满秩/唯一性检查/固定参数/额外识别约束之一；每个参数必须保留来源和估计方法。不得回避审核项，也不得虚构数据。执行文件清单由系统管理，不得输出 required_outputs。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "previous_model": previous_model.model_dump(mode="json"),
                "model_review": review.model_dump(mode="json"),
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
        data_profile: dict,
    ) -> GeneratedProgram:
        data_files = sorted(
            path.relative_to(work_dir).as_posix()
            for path in (work_dir / "data").rglob("*")
            if path.is_file()
        ) if (work_dir / "data").is_dir() else []
        deterministic_program = build_wuyi_traffic_flow_program(
            approved_model=approved_model,
            data_profile=data_profile,
        )
        if deterministic_program is not None:
            return deterministic_program
        return await _request_model(
            llm=self.llm,
            model_type=GeneratedProgram,
            system_prompt="""你是一次性求解程序生成器。只输出 GeneratedProgram JSON，其中 source_code 是完整可运行的 Python solve.py，declared_outputs 必须按原顺序逐项等于 approved_model.required_outputs。程序必须直接读取给定数据文件，执行完整拟合或优化并写出全部 declared_outputs。四类 JSON 必须严格遵守以下协议且不得增加字段：parameter_registry.json={parameters:[{id,symbol,name,value,unit,source_type,source_ref,trust_status}],summary:{...}}；result_registry.json={verified_results:[{id,result_type,claim_text,metrics:{数值指标},source_data:[路径]}],blocked_results:[{id,message,reason,source_data}],summary:{...}}；summary 必须是 JSON object，不能是字符串；parameter_registry.summary 至少包含 total_count 和 coverage_status；result_registry.summary 至少包含 verified_count、blocked_count、coverage_status、reader_decision_guide、method_route_comparison、identifiability_analysis、claim_registry、final_answer_tables。reader_decision_guide 必须回答观测量/未知量/时间口径、能否唯一恢复、推荐路线、可填内容和结论来源分层；method_route_comparison 必须列出推荐路线、被排除路线、排除原因、适用边界和证据来源；identifiability_analysis 必须说明观测方程、参数维数、rank/Jacobian/设计矩阵检查、自由度、解族或规范化假设；claim_registry 必须把结论标为题设锁定、证据自洽、依赖先验的代表解、候选实验设计或阻断项；final_answer_tables 必须给出可直接填表的函数/数值/观测时刻并绑定 result_id 或 blocked reason。若只观测聚合量且存在不可唯一分解，必须写“不可唯一识别/规范化代表解”，不能把固定参数、正则化或小残差伪装成真实唯一反推。result_registry 中 regression verified_result.metrics 必须包含 rmse 和 max_abs_residual，且数值必须能由 solver_results.regression evidence 复算；优化结果 metrics 必须包含 objective_value；预测结果 metrics 必须包含 forecast_value、train_rmse、holdout_rmse。figure_registry.json={figures:[{path,role,source_data:[路径],linked_result_ids:[结果ID]}]}，role 只能是 data_overview/model_explanation/result_fit/verification_diagnostic；solver_results.json={evidence:[...]}, 每项必须有 kind、subproblem_id、result_id；solver_results.evidence 不允许 kind=blocked，阻断原因只能写入 result_registry.blocked_results。回归 evidence 使用 kind=regression、observed、predicted、residuals、constraint_values、parameter_stability={method,max_relative_change,threshold}；observed、predicted、residuals、constraint_values 都必须是非空数字数组；constraint_values 必须是非空数字数组，不能是 object、空数组或缺省值；constraint_values 不能使用 residuals、errors、y-yhat 或任何残差/误差数组，必须记录非负约束裕度或模型中的非负量；source_code 中禁止出现 placeholder/not implemented/would need to compute，以及“简化/占位/未实现/需要另行计算/由于复杂暂不实现”等占位实现说明；无法完整计算的子问题只能写入 blocked_results，不能写半成品 verified_result 或空 evidence。优化 evidence 使用 kind=optimization、objective_value、baseline_objective、constraints=[{name,value,lower_bound或upper_bound}]、sensitivity={method,max_relative_change,threshold}。solver evidence 的 result_id 必须与 verified_results 一一对应；无法提供完整 typed solver evidence 的结果必须进入 blocked_results，不得进入 verified_results。禁止工具调用、网络、subprocess、eval/exec，也禁止运行期间补代码。""",
            payload={
                "approved_model": approved_model.model_dump(mode="json"),
                "data_files": data_files,
                "data_profile": data_profile,
                "program_generation_rules": [
                    "Use the exact file paths, workbook sheet names, column names, sample rows, and row/column sizes from data_profile.",
                    "Do not invent schemas, example data, placeholder data, simulated data, random data, or fallback data.",
                    "If the profiled data is insufficient for a calculation, write the reason to blocked_results instead of fabricating numeric results.",
                    "Required outputs whose paths are root filenames must be written exactly at the work directory root, for example solver_results.json must be written as solver_results.json.",
                    "Do not prefix root required outputs with output/, outputs/, results/, or result/; those directories are only allowed when the declared output path itself contains that directory.",
                    "At the start of main(), create every parent directory needed by declared_outputs before saving figures or JSON files.",
                    "Wrap each subproblem calculation so one failed subproblem is recorded in blocked_results and does not prevent writing parameter_registry.json, result_registry.json, figure_registry.json, and solver_results.json.",
                    "Only include a figure in figure_registry when the file is actually written; every listed figure path must exist before the program exits.",
                    "Figures may only link to verified_results IDs, never blocked_results IDs or subproblem IDs.",
                    "If there are no verified_results, figure_registry.figures must be empty because no figure has verified result evidence to bind.",
                    "Do not put planned-but-unwritten figures in figure_registry; omit them and explain the missing calculation in blocked_results.",
                    "If a verified result cannot supply typed solver evidence, matching metrics, and traceable source_data, put it in blocked_results instead of verified_results.",
                    "For constrained optimization, use scipy.optimize.minimize with a supported constrained method such as SLSQP; do not pass constraints to scipy.optimize.least_squares.",
                    "Every numeric claim in result_registry must be traceable to the real data file, the generated code, or an explicit assumption registered in parameter_registry.",
                ],
            },
            agent_name="GuidanceProgramGenerator",
            artifact_dir=work_dir,
            semantic_validator=lambda program: validate_generated_program_source(
                program.source_code,
                program.declared_outputs,
            ),
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
        text = " ".join([*subproblem.constraints, *subproblem.solve_steps]).lower()
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
