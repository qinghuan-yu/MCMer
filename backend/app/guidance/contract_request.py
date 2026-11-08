"""Validated LLM contract generation with one complete regeneration attempt."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

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
    """Raised after both complete contract generations fail validation."""


class ContractRequestTimeout(RuntimeError):
    """Raised when one project-bounded LLM transport attempt times out."""


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
    ) -> None:
        self.llm = llm
        self.model_type = model_type
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.artifact_dir = artifact_dir
        self.enrich_payload = enrich_payload
        self.semantic_validator = semantic_validator
        self.checkpoint_context = checkpoint_context
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

        for attempt_number in (1, 2):
            for transport_attempt in (1, 2):
                try:
                    response = await self._chat_once(
                        history=history,
                        tools=None,
                        tool_choice="none",
                        agent_name=self.agent_name,
                        sub_title=self.agent_name,
                    )
                except ContractRequestTimeout as exc:
                    attempts.append(_attempt_record(
                        attempt_number,
                        "",
                        exc,
                        transport_attempt=transport_attempt,
                        request_timeout=self.request_timeout,
                    ))
                    self._write_diagnostic(
                        attempts,
                        status="retrying" if transport_attempt == 1 else "failed",
                    )
                    if transport_attempt == 2:
                        raise
                    continue
                except Exception as exc:
                    attempts.append(_attempt_record(
                        attempt_number,
                        "",
                        exc,
                        transport_attempt=transport_attempt,
                        request_timeout=self.request_timeout,
                    ))
                    self._write_diagnostic(attempts, status="failed")
                    raise
                break
            raw_response = str(response.choices[0].message.content or "")
            try:
                parsed = _extract_json(raw_response)
                if self.enrich_payload is not None:
                    parsed = self.enrich_payload(parsed)
                contract = self.model_type.model_validate(parsed)
                if self.semantic_validator is not None:
                    self.semantic_validator(contract)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                attempts.append(_attempt_record(
                    attempt_number,
                    raw_response,
                    exc,
                    transport_attempt=transport_attempt,
                    request_timeout=self.request_timeout,
                ))
                self._write_diagnostic(attempts, status="retrying" if attempt_number == 1 else "failed")
                if attempt_number == 2:
                    raise ContractGenerationError(
                        f"{self.agent_name} failed to generate valid {self.model_type.__name__} "
                        f"after 2 complete attempts: {type(exc).__name__}: {exc}"
                    ) from exc
                history = self._regeneration_history(
                    original_payload=payload,
                    raw_response=raw_response,
                    error=exc,
                )
                continue

            attempts.append({
                "attempt": attempt_number,
                "transport_attempt": transport_attempt,
                "status": "valid",
                "request_timeout_seconds": self.request_timeout,
            })
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
    ) -> list[dict[str, str]]:
        correction = {
            "instruction": "完整重新生成一个合法 JSON 对象；不要局部修改、不要解释、不要 Markdown。",
            "target_schema": self.model_type.model_json_schema(),
            "validation_error_type": type(error).__name__,
            "validation_error": str(error),
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
                    "attempts": attempts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _attempt_record(
    attempt: int,
    raw_response: str,
    error: Exception,
    *,
    transport_attempt: int,
    request_timeout: int | float | None,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "transport_attempt": transport_attempt,
        "raw_response": raw_response,
        "error_type": type(error).__name__,
        "error": str(error),
        "request_timeout_seconds": request_timeout,
    }


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
