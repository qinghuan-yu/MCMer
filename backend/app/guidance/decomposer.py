"""Guidance 职责边界：decomposer。"""

from __future__ import annotations

from app.core.llm.llm import LLM
from app.core.workflow_budget import ContractTokenBudget
from app.guidance.contract_request import StructuredContractRequester
from app.guidance.contracts import (
    ProblemFactsGenerationDraft,
    ProblemSpec,
    ProblemStructureGenerationDraft,
)
from app.guidance.decomposition_context import (
    _compact_fact_evidence_profile,
    _compact_fact_input_catalog,
    _project_registered_data_sources,
)
from app.guidance.evidence_consistency import validate_problem_evidence_consistency
from app.guidance.modeling_context import _task_artifact_dir


class LLMProblemDecomposer:
    def __init__(
        self,
        llm: LLM,
        *,
        request_timeout: int | float | None = None,
        structure_token_budget: ContractTokenBudget | None = None,
        facts_token_budget: ContractTokenBudget | None = None,
    ) -> None:
        self.llm = llm
        self.request_timeout = request_timeout
        self.structure_token_budget = structure_token_budget
        self.facts_token_budget = facts_token_budget

    async def decompose(
        self,
        *,
        question: str,
        evidence_profile: dict,
        input_catalog: dict,
        task: dict,
    ) -> ProblemSpec:
        structure_requester = StructuredContractRequester(
            llm=self.llm,
            model_type=ProblemStructureGenerationDraft,
            system_prompt="""你是数学建模拆题专家。只输出题目结构 JSON，不输出 locked_facts、uncertainties、required_fact_ids 或 required_uncertainty_ids。拆出全部子问题、目标、对象、输入、约束、输出要求、问题类型、候选方向、数据来源与来源需求。每个子问题必须显式填写 depends_on_subproblem_ids，只登记题意要求的真实前置关系，不得根据描述猜测事实标识符。data_sources 只登记 evidence_profile.evidence 中的结构化表格或上传文件，其 source_ref 必须精确使用已登记值；普通题面正文不是 data source。evidence_profile 中登记的来源不得写成缺失。不得提出最终参数。""",
            agent_name="GuidanceDecomposerStructure",
            artifact_dir=_task_artifact_dir(task),
            enrich_payload=lambda payload: {**payload, "task_summary": question},
            request_timeout=self.request_timeout,
            token_budget=self.structure_token_budget,
        )
        structure = await structure_requester.request({
            "question": question,
            "evidence_profile": evidence_profile,
            "input_catalog": input_catalog,
        })
        structure = _project_registered_data_sources(
            structure,
            evidence_profile,
        )
        facts_requester = StructuredContractRequester(
            llm=self.llm,
            model_type=ProblemFactsGenerationDraft,
            system_prompt="""你是数学建模题面事实审计专家。只输出事实与歧义 JSON，不重复题目结构。题面正文和短参数表中的每个数值条件、时间范围、比例、边界、成本和离散规则都必须登记为 locked fact，不能只留在子问题描述中。大型表格或时序数据必须作为一个数据集级 locked fact 登记并引用其 source_ref，不得逐行或逐单元格展开；content_omitted=true 表示原始单元格完整保存在正式 InputCatalog/EvidenceProfile 中。每条 locked fact 或 uncertainty 必须使用稳定 ID、规范化 semantic_key/value，明确列出适用的 subproblem_ids，并逐项精确引用 input_catalog.blocks 或 evidence_profile.evidence 中已登记的 source_ref；同一 semantic_key 在重叠作用域内不得给出冲突值。data_sources 的每个 source_ref 必须由至少一个 locked fact 或 uncertainty 引用。不得把 data_source.id 当作 fact_id，不得输出 required_fact_ids 或 required_uncertainty_ids；系统会根据 subproblem_ids 确定性投影这些引用。不得提出题面没有给出的最终参数。""",
            agent_name="GuidanceDecomposerFacts",
            artifact_dir=_task_artifact_dir(task),
            semantic_validator=lambda facts: validate_problem_evidence_consistency(
                structure.promote(facts),
                evidence_profile,
                input_catalog,
            ),
            request_timeout=self.request_timeout,
            token_budget=self.facts_token_budget,
        )
        facts = await facts_requester.request({
            "subproblems": [
                item.model_dump(mode="json")
                for item in structure.subproblems
            ],
            "data_sources": [
                item.model_dump(mode="json")
                for item in structure.data_sources
            ],
            "evidence_profile": _compact_fact_evidence_profile(evidence_profile),
            "input_catalog": _compact_fact_input_catalog(input_catalog),
        })
        return structure.promote(facts)
