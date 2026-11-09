"""Validated LLM contracts with a two-call budget and explicit truncation handling."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.workflow_budget import ContractTokenBudget
from app.guidance.recovery import RecoveryCheckpoint


ModelT = TypeVar("ModelT", bound=BaseModel)


STRICT_JSON_RULES = (
    "Strict JSON output rules:\n"
    "- Return one JSON object only.\n"
    "- Do not wrap the JSON in Markdown code fences.\n"
    "- Do not add explanations before or after the JSON.\n"
    "- Escape every quote inside string values.\n"
    "- Do not use raw newline characters inside string values; use \\n when needed.\n"
    "- Use double quotes for every JSON key and string value."
)


class ContractGenerationError(RuntimeError):
    """Raised when a complete valid contract cannot be obtained within the budget."""


class ContractRequestTimeout(RuntimeError):
    """Raised when one project-bounded LLM transport attempt times out."""


class ContractTruncationError(ContractGenerationError):
    """输出预算耗尽；即使正文可解析，也不能作为完整合同。"""


class StructuredContractRequester(Generic[ModelT]):
    def __init__(
        self,
        *,
        llm,
        model_type: type[ModelT],
        system_prompt: str,
        agent_name: str,
        artifact_dir: Path | None = None,
        enrich_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        semantic_validator: Callable[[ModelT], None] | None = None,
        request_timeout: int | float | None = None,
        checkpoint_context: Any = None,
        token_budget: ContractTokenBudget | None = None,
    ) -> None:
        self.llm = llm
        self.model_type = model_type
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.artifact_dir = artifact_dir
        self.enrich_payload = enrich_payload
        self.semantic_validator = semantic_validator
        self.checkpoint_context = checkpoint_context
        self.initial_tokens = token_budget.initial if token_budget is not None else getattr(llm, "max_tokens", None)
        self.token_ceiling = token_budget.ceiling if token_budget is not None else self.initial_tokens
        self.request_timeout = (
            request_timeout
            if request_timeout is not None
            else getattr(llm, "request_timeout", None)
        )

    async def request(self, payload: Any) -> ModelT:
        attempts: list[dict[str, Any]] = []
        history = self._initial_history(payload)
        checkpoint = RecoveryCheckpoint(self.artifact_dir, "contracts", self.agent_name)
        context = {
            "history": history,
            "dependencies": self.checkpoint_context,
            "token_budget": {"initial": self.initial_tokens, "ceiling": self.token_ceiling},
            "llm": {name: getattr(self.llm, name, None) for name in (
                "model", "base_url", "api_base", "max_tokens", "temperature", "top_p", "reasoning_effort",
            )},
        }
        cached = checkpoint.load(context)
        if cached is not None:
            try:
                contract = self.model_type.model_validate(cached)
                if self.semantic_validator is not None:
                    self.semantic_validator(contract)
                return contract
            except ValueError:
                pass
        checkpoint.discard()

        attempt_number, transport_attempt = 1, 1
        request_tokens = self.initial_tokens
        for call_number in (1, 2):
            record = {
                "attempt": attempt_number, "transport_attempt": transport_attempt,
                "request_timeout_seconds": self.request_timeout,
                "request_max_tokens": request_tokens,
                "finish_reason": None, "usage": None,
            }
            attempts.append(record)
            try:
                response = await self._chat_once(
                    history=history, tools=None, tool_choice="none",
                    agent_name=self.agent_name, sub_title=self.agent_name,
                    max_attempts=1,
                    max_tokens=request_tokens,
                )
            except ContractRequestTimeout as exc:
                record.update(_failure_record("", exc, "timeout"))
                self._write_diagnostic(attempts, status="retrying" if call_number == 1 else "failed")
                if call_number == 2:
                    raise
                transport_attempt += 1
                continue
            except Exception as exc:
                record.update(_failure_record("", exc, "transport"))
                self._write_diagnostic(attempts, status="failed")
                raise
            raw_response = str(response.choices[0].message.content or "")
            record.update(finish_reason=getattr(response.choices[0], "finish_reason", None),
                          usage=_response_usage(response))
            if record["finish_reason"] == "length":
                exc = ContractTruncationError(f"{self.agent_name}: output truncated at max_tokens={request_tokens}")
                record.update(_failure_record(raw_response, exc, "truncation"))
                usage = record["usage"] or {}
                record["truncation_kind"] = (
                    "reasoning_exhausted" if not raw_response.strip() and usage.get("completion_tokens")
                    and usage.get("reasoning_tokens") == usage["completion_tokens"] else "output_truncated"
                )
                can_recover = call_number == 1 and request_tokens is not None and request_tokens < self.token_ceiling
                self._write_diagnostic(attempts, status="retrying" if can_recover else "failed")
                if can_recover:
                    # 截断没有有效语义反馈；用原始完整请求和预设额度重生成一次。
                    request_tokens = self.token_ceiling
                    attempt_number += 1
                    transport_attempt = 1
                    continue
                raise exc
            failure_kind = "schema"
            try:
                parsed = _extract_json(raw_response)
                if self.enrich_payload is not None:
                    parsed = self.enrich_payload(parsed)
                contract = self.model_type.model_validate(parsed)
                failure_kind = "semantic"
                if self.semantic_validator is not None:
                    self.semantic_validator(contract)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                if isinstance(exc, json.JSONDecodeError):
                    failure_kind = "syntax"
                record.update(_failure_record(raw_response, exc, failure_kind))
                self._write_diagnostic(attempts, status="retrying" if call_number == 1 else "failed")
                if call_number == 2:
                    raise ContractGenerationError(
                        f"{self.agent_name} failed to generate valid {self.model_type.__name__} "
                        f"after {attempt_number} complete attempts / {call_number} provider calls: {type(exc).__name__}: {exc}"
                    ) from exc
                history = self._regeneration_history(
                    original_payload=payload,
                    raw_response=raw_response,
                    error=exc,
                    failure_kind=failure_kind,
                )
                attempt_number += 1
                transport_attempt = 1
                continue

            record["status"] = "valid"
            self._write_diagnostic(
                attempts,
                status="valid" if len(attempts) == 1 else "recovered",
            )
            checkpoint.save(context, contract.model_dump(mode="json", exclude_computed_fields=True))
            return contract

        raise AssertionError("contract request attempt loop did not return")

    async def _chat_once(self, **kwargs: Any) -> Any:
        timeout = self.request_timeout
        if timeout is None:
            return await self.llm.chat(**kwargs)
        try:
            return await asyncio.wait_for(self.llm.chat(**kwargs), timeout=float(timeout))
        except asyncio.TimeoutError as exc:
            raise ContractRequestTimeout(
                f"{self.agent_name}: request timed out after {timeout}s"
            ) from exc

    def _initial_history(self, payload: Any) -> list[dict[str, str]]:
        schema = json.dumps(self.model_type.model_json_schema(), ensure_ascii=False)
        return [
            {
                "role": "system",
                "content": f"{self.system_prompt}\n\n{STRICT_JSON_RULES}\n\n目标 JSON Schema：{schema}",
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def _regeneration_history(
        self,
        *,
        original_payload: Any,
        raw_response: str,
        error: Exception,
        failure_kind: str,
    ) -> list[dict[str, str]]:
        correction = {
            "instruction": "完整重新生成一个合法 JSON 对象；不要局部修改、不要解释、不要 Markdown。",
            "target_schema": self.model_type.model_json_schema(),
            "validation_error_type": type(error).__name__,
            "validation_error": str(error),
            "failure_kind": failure_kind,
            "original_input": original_payload,
        }
        return [
            {
                "role": "system",
                "content": (
                    f"{self.system_prompt}\n\n"
                    f"你正在重新生成 {self.model_type.__name__}。必须完整重新生成并严格满足 JSON Schema，"
                    "同时保留上述所有业务约束。\n\n"
                    f"{STRICT_JSON_RULES}"
                ),
            },
            {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
        ]

    def _write_diagnostic(self, attempts: list[dict[str, Any]], *, status: str) -> None:
        if self.artifact_dir is None:
            return
        directory = self.artifact_dir / "contract_diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.agent_name}.json"
        path.write_text(
            json.dumps(
                {
                    "agent_name": self.agent_name,
                    "contract": self.model_type.__name__,
                    "status": status,
                    "request_timeout_seconds": self.request_timeout,
                    "max_provider_calls": 2,
                    "provider_calls": len(attempts),
                    "reserved_completion_tokens": (
                        sum(item["request_max_tokens"] for item in attempts)
                        if all(item["request_max_tokens"] is not None for item in attempts) else None
                    ),
                    "max_reserved_completion_tokens": (
                        self.initial_tokens + self.token_ceiling if self.initial_tokens is not None else None
                    ),
                    "usage_complete": all(
                        (item["usage"] or {}).get(key) is not None
                        for item in attempts for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    ),
                    "usage": _total_usage(attempts),
                    "attempts": attempts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _failure_record(raw_response: str, error: Exception, failure_kind: str) -> dict[str, Any]:
    return {
        "raw_response": raw_response,
        "error_type": type(error).__name__,
        "error": str(error),
        "failure_kind": failure_kind,
    }


def _response_usage(response: Any) -> dict[str, int | None] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _total_usage(attempts: list[dict[str, Any]]) -> dict[str, int | None]:
    # 只累计服务端已报告的用量；reasoning属于completion，不能再加入total。
    totals = {}
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
        values = [(item["usage"] or {}).get(key) for item in attempts]
        known = [value for value in values if value is not None]
        totals[key] = sum(known) if known else None
    return totals


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
