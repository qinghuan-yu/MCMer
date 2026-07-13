"""Versioned contracts for reusable mathematical operators."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperatorContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperatorPort(OperatorContract):
    value_kind: Literal["scalar", "vector", "matrix", "table", "artifact"]
    unit: str = Field(min_length=1, max_length=80)


class OperatorDefinition(OperatorContract):
    operator_id: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=40)
    inputs: dict[str, OperatorPort]
    outputs: dict[str, OperatorPort] = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    deterministic: bool
    validator_id: str = Field(min_length=1, max_length=160)


@dataclass(frozen=True)
class RegisteredOperator:
    definition: OperatorDefinition
    executor: Callable | None
    validator: Callable | None


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, RegisteredOperator] = {}

    def register(
        self,
        definition: OperatorDefinition,
        *,
        executor: Callable | None = None,
        validator: Callable | None = None,
    ) -> None:
        if definition.operator_id in self._operators:
            raise ValueError(f"operator already registered: {definition.operator_id}")
        self._operators[definition.operator_id] = RegisteredOperator(
            definition=definition,
            executor=executor,
            validator=validator,
        )

    def get(self, operator_id: str) -> OperatorDefinition | None:
        registered = self._operators.get(operator_id)
        return registered.definition if registered is not None else None

    def runtime(self, operator_id: str) -> RegisteredOperator | None:
        return self._operators.get(operator_id)
