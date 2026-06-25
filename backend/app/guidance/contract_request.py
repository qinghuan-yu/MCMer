"""Validated LLM contract generation with one complete regeneration attempt."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


class ContractGenerationError(RuntimeError):
    """Raised after both complete contract generations fail validation."""


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
    ) -> None:
        self.llm = llm
        self.model_type = model_type
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.artifact_dir = artifact_dir
        self.enrich_payload = enrich_payload

    async def request(self, payload: Any) -> ModelT:
        attempts: list[dict[str, str | int]] = []
        history = self._initial_history(payload)

        for attempt_number in (1, 2):
            try:
                response = await self.llm.chat(
                    history=history,
                    tools=None,
                    tool_choice="none",
                    agent_name=self.agent_name,
                    sub_title=self.agent_name,
                )
            except Exception as exc:
                attempts.append(_attempt_record(attempt_number, "", exc))
                self._write_diagnostic(attempts, status="failed")
                raise
            raw_response = str(response.choices[0].message.content or "")
            try:
                parsed = _extract_json(raw_response)
                if self.enrich_payload is not None:
                    parsed = self.enrich_payload(parsed)
                contract = self.model_type.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                attempts.append(_attempt_record(attempt_number, raw_response, exc))
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

            if attempts:
                attempts.append({
                    "attempt": attempt_number,
                    "raw_response": raw_response,
                    "status": "valid",
                })
                self._write_diagnostic(attempts, status="recovered")
            return contract

        raise AssertionError("contract request attempt loop did not return")

    def _initial_history(self, payload: Any) -> list[dict[str, str]]:
        schema = json.dumps(self.model_type.model_json_schema(), ensure_ascii=False)
        return [
            {
                "role": "system",
                "content": f"{self.system_prompt}\n目标 JSON Schema：{schema}",
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
            "previous_invalid_response": raw_response,
            "original_input": original_payload,
        }
        return [
            {
                "role": "system",
                "content": (
                    f"你正在重新生成 {self.model_type.__name__}。必须完整重新生成并严格满足 JSON Schema。"
                ),
            },
            {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
        ]

    def _write_diagnostic(self, attempts: list[dict[str, str | int]], *, status: str) -> None:
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
                    "attempts": attempts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _attempt_record(attempt: int, raw_response: str, error: Exception) -> dict[str, str | int]:
    return {
        "attempt": attempt,
        "raw_response": raw_response,
        "error_type": type(error).__name__,
        "error": str(error),
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
