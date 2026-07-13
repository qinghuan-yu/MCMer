"""
响应消息模型
"""
from typing import Any, Literal, Optional
from pydantic import BaseModel
from pydantic import model_validator


class SystemMessage(BaseModel):
    """系统消息 (通过 WebSocket 推送)"""
    content: str
    type: str = "info"  # info, warning, error, success
    agent: str = "system"
    data: Optional[Any] = None


class InterpreterMessage(BaseModel):
    """代码解释器消息"""
    type: str  # code, result, error, image
    content: str
    agent: str = "coder"
    section: str = ""


class WriterMessage(BaseModel):
    """写作手消息"""
    type: str  # text, citation, section_complete
    content: str
    agent: str = "writer"
    section: str = ""


class TaskProgress(BaseModel):
    """任务进度"""
    task_id: str
    status: str
    stage: str  # coordinator, modeling, coding, writing, done, revision
    progress: float = 0.0  # 0.0 - 1.0
    message: str = ""
    current_subtask: str = ""


class VerificationLayers(BaseModel):
    execution_verified: str
    evidence_supported: str
    model_adequate: str


def extract_verification_layers(report: Any) -> dict[str, str]:
    report = report if isinstance(report, dict) else {}
    layers: dict[str, str] = {}
    for key in (
        "execution_verified",
        "evidence_supported",
        "model_adequate",
    ):
        layer = report.get(key)
        status = layer.get("status") if isinstance(layer, dict) else None
        layers[key] = str(status or "not_assessed")
    return layers


class SubproblemCapability(BaseModel):
    subproblem_id: str
    coverage_status: Literal["complete", "partial", "blocked"]
    model_families: list[str] = []
    required_operators: list[str] = []
    missing_operators: list[str] = []
    blocking_reasons: list[str] = []
    recovery_actions: list[str] = []


def extract_capability_coverage(
    *,
    approved_model: Any,
    compilation_result: Any,
    result_registry: Any,
) -> list[dict[str, Any]]:
    approved_model = approved_model if isinstance(approved_model, dict) else {}
    model_ir = approved_model.get("approved_model_ir", approved_model)
    model_ir = model_ir if isinstance(model_ir, dict) else {}
    compilation_result = compilation_result if isinstance(compilation_result, dict) else {}
    result_registry = result_registry if isinstance(result_registry, dict) else {}
    subproblems = model_ir.get("subproblems")
    if not isinstance(subproblems, list):
        return []

    diagnostics = compilation_result.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    verified_results = result_registry.get("verified_results")
    verified_results = verified_results if isinstance(verified_results, list) else []
    blocked_results = result_registry.get("blocked_results")
    blocked_results = blocked_results if isinstance(blocked_results, list) else []

    def unique_text(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    coverage: list[dict[str, Any]] = []
    for raw_subproblem in subproblems:
        if not isinstance(raw_subproblem, dict):
            continue
        subproblem_id = str(raw_subproblem.get("subproblem_id") or "").strip()
        if not subproblem_id:
            continue
        execution_status = str(raw_subproblem.get("execution_status") or "blocked")
        operators = raw_subproblem.get("operators")
        operators = operators if isinstance(operators, list) else []
        node_operators = {
            str(item.get("node_id") or ""): str(item.get("operator_id") or "")
            for item in operators
            if isinstance(item, dict)
        }
        required_operators = unique_text(list(node_operators.values()))
        validation = raw_subproblem.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        model_families = unique_text(validation.get("model_families") or [])
        subproblem_diagnostics = [
            item for item in diagnostics
            if isinstance(item, dict) and item.get("subproblem_id") == subproblem_id
        ]
        missing_operators = unique_text([
            node_operators.get(str(item.get("node_id") or ""), "")
            for item in subproblem_diagnostics
            if item.get("code") == "operator_not_registered"
        ])
        subproblem_verified = [
            item for item in verified_results
            if isinstance(item, dict) and item.get("subproblem_id") == subproblem_id
        ]
        subproblem_blocked = [
            item for item in blocked_results
            if isinstance(item, dict) and item.get("subproblem_id") == subproblem_id
        ]
        blocking_reasons = unique_text([
            raw_subproblem.get("blocking_reason") if execution_status == "blocked" else "",
            *(item.get("message") or item.get("reason") for item in subproblem_diagnostics),
            *(item.get("message") or item.get("reason") for item in subproblem_blocked),
        ])
        recovery_actions = unique_text([
            raw_subproblem.get("recovery_action") if execution_status == "blocked" else "",
            *(item.get("recovery_action") for item in subproblem_diagnostics),
            *(item.get("recovery_action") for item in subproblem_blocked),
        ])
        if missing_operators and not recovery_actions:
            recovery_actions.append(
                "Register validated problem-independent operators for the missing capabilities, then recompile."
            )
        has_blocking = execution_status == "blocked" or bool(
            subproblem_diagnostics or subproblem_blocked
        )
        if has_blocking:
            coverage_status = "partial" if subproblem_verified else "blocked"
        elif subproblem_verified:
            coverage_status = "complete"
        else:
            coverage_status = "blocked"
            blocking_reasons.append("No registered result is available for this subproblem.")
            recovery_actions.append("Run the compiled execution graph and verify its registered result.")
        coverage.append(SubproblemCapability(
            subproblem_id=subproblem_id,
            coverage_status=coverage_status,
            model_families=model_families,
            required_operators=required_operators,
            missing_operators=missing_operators,
            blocking_reasons=unique_text(blocking_reasons),
            recovery_actions=unique_text(recovery_actions),
        ).model_dump())
    return coverage


class TaskResult(BaseModel):
    """任务结果"""
    task_id: str
    status: str
    task_type: str = "writing"
    primary_artifact_type: str = "paper"
    markdown_path: str = ""
    guidance_path: str = ""
    paper_path: str = ""
    docx_path: str = ""
    notebook_path: str = ""
    work_dir: str = ""
    error_message: str = ""
    error_code: str = ""
    error_type: str = ""
    error_details: Optional[Any] = None
    audit_status: str = ""
    audit_summary: str = ""
    audit_blocks: Optional[Any] = None
    verification_layers: Optional[VerificationLayers] = None
    capability_coverage: list[SubproblemCapability] = []

    @model_validator(mode="after")
    def fill_markdown_aliases(self) -> "TaskResult":
        if not self.markdown_path:
            self.markdown_path = self.guidance_path or self.paper_path
        if self.primary_artifact_type == "guidance" and not self.guidance_path:
            self.guidance_path = self.markdown_path or self.paper_path
            self.docx_path = ""
        if not self.paper_path:
            self.paper_path = self.markdown_path or self.guidance_path
        return self


# ============================================================
# 历史 & 修订相关模型
# ============================================================

class HistoryTaskInfo(BaseModel):
    """历史任务摘要"""
    task_id: str
    question: str = ""
    status: str = "unknown"
    task_type: str = "writing"
    created_at: str = ""
    has_paper: bool = False
    has_guidance: bool = False
    has_notebook: bool = False
    revision_count: int = 0
    is_revision: bool = False
    parent_task_id: str = ""


class PaperContent(BaseModel):
    """论文内容"""
    task_id: str
    question: str = ""
    primary_artifact_type: str = "paper"
    markdown_path: str = ""
    paper: Optional[str] = None
    guidance: Optional[str] = None
    latest_paper: Optional[str] = None
    latest_guidance: Optional[str] = None
    paper_version: int = 0
    status: str = "unknown"
    created_at: str = ""
    audit_status: str = ""
    audit_summary: str = ""
    audit_blocks: Optional[Any] = None
    verification_layers: Optional[VerificationLayers] = None
    capability_coverage: list[SubproblemCapability] = []


class RevisionRequest(BaseModel):
    """用户修订请求"""
    task_id: str
    feedback: str  # 用户的修改建议
    revise_code: bool = False  # 是否需要修改代码
    workflow_mode: str = "standard"
    sections: Optional[list[str]] = None  # 指定修订的章节


class RevisionResult(BaseModel):
    """修订结果"""
    task_id: str
    original_task_id: str
    paper_content: str = ""
    paper_path: str = ""
    version: int = 1
    status: str = "completed"
