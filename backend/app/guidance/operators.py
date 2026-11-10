"""Versioned contracts for reusable mathematical operators."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.guidance.operator_parameters import OperatorParameters


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
    parameter_model: type[OperatorParameters] = Field(exclude=True)
    system_managed: bool = False

    @computed_field
    @property
    def description(self) -> str:
        return self.parameter_model.__doc__ or ""

    @computed_field
    @property
    def parameters_schema(self) -> dict:
        return self.parameter_model.model_json_schema()

    def validate_call(
        self, *, inputs: dict, parameters: dict, outputs: list[str] | None = None,
    ) -> dict:
        parsed = self.parameter_model.model_validate(parameters)
        expected = parsed.input_names(set(self.inputs))
        if set(inputs) != expected:
            raise ValueError(f"expected input ports {sorted(expected)}, got {sorted(inputs)}")
        if outputs is not None and (
            len(outputs) != len(self.outputs) or len(set(outputs)) != len(outputs)
        ):
            raise ValueError("output aliases must be unique and match the registered port count")
        return parsed.model_dump(mode="json")


@dataclass(frozen=True)
class RegisteredOperator:
    definition: OperatorDefinition
    executor: Callable | None
    validator: Callable | None

    def validate_outputs(self, outputs: object) -> None:
        if not isinstance(outputs, dict) or set(outputs) != set(self.definition.outputs):
            raise ValueError("operator output ports do not match registration")
        if self.validator is None:
            raise RuntimeError("operator validator is not registered")
        issues = self.validator(outputs=outputs)
        if issues:
            raise ValueError("operator validation failed: " + "; ".join(issues))

    def invoke(self, *, inputs: dict, parameters: dict, random_seed, work_dir) -> dict:
        normalized = self.definition.validate_call(inputs=inputs, parameters=parameters)
        if self.executor is None:
            raise RuntimeError("operator runtime is not registered")
        if not self.definition.deterministic and random_seed is None:
            raise ValueError("nondeterministic operator requires an explicit random seed")
        outputs = self.executor(
            inputs=inputs, parameters=normalized, random_seed=random_seed, work_dir=work_dir,
        )
        self.validate_outputs(outputs)
        return outputs


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

    def definitions(self) -> list[OperatorDefinition]:
        return [self._operators[key].definition for key in sorted(self._operators)]

    def contract_sha256(self) -> str:
        payload = [item.model_dump(mode="json") for item in self.definitions()]
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def model_contracts(self) -> list[dict]:
        # 编译器托管的 MILP 只发布可用性与入口，不向模型开放内部系数参数。
        return [
            item.model_dump(
                mode="json", exclude={"parameters_schema"} if item.system_managed else set(),
            ) for item in self.definitions()
        ]
