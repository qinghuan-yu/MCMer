"""operator_runtimes: output_contracts 领域实现与合同。"""

from __future__ import annotations

from collections.abc import Callable


def mapping_output_validator(name: str) -> Callable:
    def validate(*, outputs: dict) -> list[str]:
        if set(outputs) != {name}:
            return [f"expected exactly one output named {name}"]
        if not isinstance(outputs[name], dict):
            return [f"output {name} must be a mapping"]
        return []

    return validate
