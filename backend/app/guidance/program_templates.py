"""Deterministic program-template registry for approved guidance models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.guidance.anchor_bolt_template import build_wuyi_anchor_bolt_program
from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
from app.guidance.traffic_flow_template import build_wuyi_traffic_flow_program


class UnsupportedProgramTemplateError(RuntimeError):
    pass


ProgramTemplate = Callable[..., GeneratedProgram | None]


class TemplateProgramGenerator:
    """Select one reviewed deterministic solver template; never generate arbitrary code."""

    def __init__(self, templates: tuple[ProgramTemplate, ...] | None = None) -> None:
        self._templates = templates or (
            build_wuyi_traffic_flow_program,
            build_wuyi_anchor_bolt_program,
        )

    async def generate(
        self,
        *,
        approved_model: ApprovedModelSpec,
        task: dict,
        work_dir: Path,
        data_profile: dict[str, Any],
    ) -> GeneratedProgram:
        for template in self._templates:
            program = template(
                approved_model=approved_model,
                data_profile=data_profile,
            )
            if program is not None:
                return program
        raise UnsupportedProgramTemplateError(
            "no deterministic solver template registered for approved model "
            f"{approved_model.model_id!r}"
        )
