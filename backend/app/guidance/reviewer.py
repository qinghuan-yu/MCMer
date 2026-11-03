"""Guidance 职责边界：reviewer。"""

from __future__ import annotations

from pathlib import Path

from app.core.llm.llm import LLM
from app.guidance.contracts import ProblemSpec
from app.guidance.generation_validation import _validate_model_ir_semantics
from app.guidance.model_generation import _request_model
from app.guidance.model_ir import ModelIR
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.model_review_validation import validate_model_review


class LLMModelReviewer:
    def __init__(
        self,
        llm: LLM,
        *,
        request_timeout: int | float | None = None,
    ) -> None:
        self.llm = llm
        self.request_timeout = request_timeout

    async def review(
        self,
        *,
        problem_spec: ProblemSpec,
        model_ir: ModelIR,
        revision_request: ModelIRReview | None = None,
        evidence_profile: dict | None = None,
        task: dict,
        work_dir: Path,
    ) -> ModelIRReview:
        _validate_model_ir_semantics(model_ir)
        return await _request_model(
            llm=self.llm,
            model_type=ModelIRReview,
            system_prompt="""你是独立 ModelIR 审核员。只输出 ModelIRReview JSON。严格审核强类型表达式、可辨识性、维度/单位、数据证据、算子前提、约束和计算可行性。subproblem_reviews 必须按 ModelIR 顺序逐题覆盖且不重不漏；每题独立决定 approved、revision_required 或 blocked，禁止用一题的问题阻断其他可解题。原本就是 blocked 的 ModelIR 子题必须保持 blocked，并保留具体 blocking_issues；只有 solvable 且本题审核通过的子题才能 approved。不要自行决定整体 status 或 executable_subproblem_ids，它们由系统从逐题决定确定性派生。逐项输出 assumption_checks：核对假设依据、受影响模型元素、替代情景和敏感性检查；没有假设时必须输出 []。首次要求修订时，在对应 subproblem_review.required_revisions 逐项给出稳定 requirement_id、description 和 acceptance_criteria，所有 revision_checks 必须为空。复审时在对应 subproblem_review.revision_checks 逐项使用相同 requirement_id 登记处置状态，并用 RFC 6901 model_paths 引用修订后 ModelIR 中实际存在的节点；任何要求未接受时该子题不得 approved。存在未接受假设的子题不得 approved，也不得把条件解写成题设唯一结论。不得把程序可运行等同于模型充分。若 IR 虚构输入、缺少基线或证据绑定、依赖自由文本公式或隐式代码，仅将受影响子题标为 revision_required 或 blocked。""",
            payload={
                "problem_spec": problem_spec.model_dump(mode="json"),
                "model_ir": model_ir.model_dump(mode="json"),
                "evidence_profile": evidence_profile or {"evidence": []},
                **(
                    {"revision_request": revision_request.model_dump(mode="json")}
                    if revision_request is not None
                    else {}
                ),
            },
            agent_name="GuidanceModelReviewer",
            artifact_dir=work_dir,
            semantic_validator=lambda review: validate_model_review(
                review,
                model_ir,
                revision_request=revision_request,
            ),
            request_timeout=self.request_timeout,
        )
