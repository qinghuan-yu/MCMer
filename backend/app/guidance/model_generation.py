"""通过 DeepSeek Harness 的工具循环构建单个可审核的 ModelIR。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.llm.llm import LLM
from app.core.workflow_budget import ContractTokenBudget
from app.guidance.contract_request import _response_usage
from app.guidance.contracts import ProblemSpec
from app.guidance.deepseek_harness import run_math_turn
from app.guidance.fact_coverage import FactScope
from app.guidance.generation_validation import _validate_model_ir_slice
from app.guidance.math_workspace import MathWorkspace
from app.guidance.model_ir import ModelIR
from app.guidance.model_ir_drafts import INDEXED_SOLUTION_OUTPUT, INDEXED_SOLVER_NODE_ID
from app.guidance.model_review_contracts import ModelIRReview
from app.guidance.modeling_context import _compact_previous_model_summary, _subproblem_review_payload
from app.guidance.operators import OperatorRegistry
from app.guidance.recovery import RecoveryCheckpoint, canonical_digest


async def _request_single_subproblem_model(
    *, llm: LLM, system_prompt: str, problem_spec: ProblemSpec,
    subproblem: dict, slice_spec: dict, evidence_profile: dict, registry: OperatorRegistry,
    request_name: str, artifact_dir: Path, previous_model: Any | None, review: ModelIRReview | None,
    architecture_timeout: int | float | None, indexed_core_timeout: int | float | None,
    indexed_constraints_timeout: int | float | None,
    architecture_token_budget: ContractTokenBudget | None = None,
    indexed_core_token_budget: ContractTokenBudget | None = None,
    indexed_constraints_token_budget: ContractTokenBudget | None = None,
) -> ModelIR:
    target = str(subproblem["id"])
    scope = FactScope.from_problem_spec(problem_spec, target)
    workspace = MathWorkspace(problem_spec=problem_spec, subproblem_id=target,
                              evidence_profile=evidence_profile, registry=registry)
    menu = workspace.tools()
    context = {
        **slice_spec, "target_subproblem_id": target, "locked_facts": scope.payload(),
        "required_fact_ids": list(scope.fact_ids), "evidence_profile": evidence_profile,
        "available_operators": registry.model_catalog(),
        "indexed_solver_answer": {"value_ref": INDEXED_SOLUTION_OUTPUT, "evidence_ids": [INDEXED_SOLVER_NODE_ID]},
        "operator_contract_sha256": registry.contract_sha256(),
    }
    if previous_model is not None:
        context["previous_model_summary"] = _compact_previous_model_summary(previous_model)
    if review is not None:
        context["model_review"] = _subproblem_review_payload(review, target)

    initial_tokens = architecture_token_budget.initial if architecture_token_budget else getattr(llm, "max_tokens", None) or 12288
    ceiling = max([initial_tokens] + [b.ceiling for b in (architecture_token_budget, indexed_core_token_budget,
                                                         indexed_constraints_token_budget) if b])
    timeout = sum(t for t in (architecture_timeout, indexed_core_timeout, indexed_constraints_timeout) if t) or 360
    max_steps = 8
    key = {"protocol": "math-workbench-v1", "prompt": system_prompt, "task": context, "tools": menu,
           "previous_model": previous_model, "max_steps": max_steps, "tokens": [initial_tokens, ceiling],
           "model": getattr(llm, "model", None), "base_url": getattr(llm, "base_url", None),
           "temperature": getattr(llm, "temperature", None)}
    if previous_model is not None:
        key["previous_model"] = previous_model.model_dump(mode="json")
    checkpoint = RecoveryCheckpoint(artifact_dir, "math_workbench", request_name)
    cached = checkpoint.load(key)
    if cached is not None:
        try:
            model = ModelIR.model_validate(cached)
            _validate_model_ir_slice(model, expected_id=target, problem_spec=problem_spec,
                                     evidence_profile=evidence_profile, registry=registry)
            return model
        except ValueError:
            pass
    checkpoint.discard()

    actions = []
    requests = []
    log_dir = artifact_dir / "contract_diagnostics"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{request_name}.json"

    def write_receipt(status, error=None):
        receipt = {"engine": "deepseek-harness", "agent_name": request_name, "status": status,
                   "provider_calls": len(requests), "requests": requests, "actions": actions,
                   "context_sha256": canonical_digest(key)}
        if error:
            receipt["error"] = str(error)
        temporary = log_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(log_path)

    def execute_tool(name, arguments):
        if len(actions) >= 32:
            raise ValueError("tool call limit reached before submission")
        observation = workspace.execute(name, arguments)
        entry = {"name": name, "ok": observation["ok"], "arguments_sha256": canonical_digest(arguments),
                 "result_sha256": canonical_digest(observation)}
        if not observation["ok"]:
            entry["error"] = observation["error"]
        actions.append(entry)
        write_receipt("editing")
        return observation

    async def request_model(history, tools):
        token_limit = initial_tokens
        while len(requests) < max_steps:
            call = {"number": len(requests) + 1, "max_tokens": token_limit, "usage": None}
            requests.append(call)
            write_receipt("requesting")
            response = await llm.chat(history=history, tools=tools, tool_choice="auto", max_attempts=1,
                                      agent_name=request_name, sub_title=request_name, max_tokens=token_limit)
            choice = response.choices[0]
            call.update(usage=_response_usage(response), finish_reason=choice.finish_reason)
            write_receipt("editing")
            if choice.finish_reason == "length":
                if token_limit < ceiling and len(requests) < max_steps:
                    token_limit = ceiling
                    continue
                raise ValueError("model output was truncated before a complete action")
            message = choice.message.model_dump(exclude_none=True)
            return {k: message[k] for k in ("role", "content", "tool_calls", "reasoning_content") if k in message}
        raise ValueError("model request limit reached before submission")

    try:
        await run_math_turn(prompt=json.dumps(context, ensure_ascii=False), system_prompt=system_prompt, tools=menu,
                            request_model=request_model, execute_tool=execute_tool, is_done=lambda: workspace.result is not None,
                            max_steps=max_steps, timeout_seconds=timeout)
        if workspace.result is None:
            last_error = next((step["error"] for step in reversed(actions) if "error" in step), "")
            raise ValueError(f"model stopped without a validated submit_model: {last_error}")
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        last_edit = next((step for step in reversed(actions) if step["name"] == "edit_model"), None)
        last_failure = next((step for step in reversed(actions) if not step["ok"]), None)
        if last_failure is not None:
            if last_failure["name"] == "submit_model" and last_edit is not None and not last_edit["ok"]:
                last_failure = last_edit
            reason = f"{reason}. Last rejected action: {last_failure['error']}"
        write_receipt("blocked", reason)
        raise RuntimeError(reason) from exc
    write_receipt("submitted")
    checkpoint.save(key, workspace.result.model_dump(mode="json"), files=[str(log_path.relative_to(artifact_dir))])
    return workspace.result
