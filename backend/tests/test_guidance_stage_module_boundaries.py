from __future__ import annotations

from app.guidance.operator_catalog import build_default_operator_registry

import importlib
import asyncio
from pathlib import Path
from typing import get_type_hints

import pytest


@pytest.mark.parametrize(
    ("owner", "symbol"),
    [
        ("decomposer", "LLMProblemDecomposer"),
        ("modeler", "LLMModeler"),
        ("reviewer", "LLMModelReviewer"),
        ("model_generation", "_request_single_subproblem_model"),
        ("generation_validation", "_validate_generation_draft_slice"),
        ("modeling_context", "_compact_problem_spec_for_modeling"),
        ("stage_contracts", "Modeler"),
        ("stage_io", "_read_json"),
        ("verification_stage", "ResultVerificationStage"),
        ("guidance_stage", "GuidanceStage"),
        ("guidance_rendering.diagnostics", "_render_calculation_failure_diagnostic"),
        ("guidance_rendering.sections", "_registered_subproblem_modeling_lines"),
        ("guidance_rendering.data", "_guidance_text"),
    ],
)
def test_generation_verification_and_rendering_have_explicit_owners(
    owner: str, symbol: str,
) -> None:
    module = importlib.import_module(f"app.guidance.{owner}")

    assert getattr(module, symbol).__module__ == module.__name__


def test_removed_agent_module_does_not_remain_as_a_forwarding_facade() -> None:
    package = Path(__file__).parents[1] / "app" / "guidance"

    assert not (package / "agents.py").exists()


def test_stage_and_rendering_entrypoints_keep_real_orchestration() -> None:
    from app.guidance.renderer import render_guidance_markdown, render_verified_guidance
    from app.guidance.stages import (
        CalculationStage,
        DecompositionStage,
        ModelingStage,
        ModelReviewStage,
    )

    for stage in (CalculationStage, DecompositionStage, ModelingStage, ModelReviewStage):
        assert stage.__module__ == "app.guidance.stages"
    for renderer in (render_guidance_markdown, render_verified_guidance):
        assert renderer.__module__ == "app.guidance.renderer"


def test_indexed_core_validator_keeps_resolvable_type_annotations() -> None:
    from app.guidance.generation_validation import _validate_indexed_core_stage

    assert "evidence_profile" in get_type_hints(_validate_indexed_core_stage)


def test_workbench_exposes_exact_static_parameter_source_schema():
    from app.guidance.contracts import ProblemSpec
    from app.guidance.math_workspace import MathWorkspace

    spec = ProblemSpec(task_summary="A finite indexed optimization.", subproblems=[{
        "id": "problem1", "required_fact_ids": [], "required_uncertainty_ids": [], "depends_on_subproblem_ids": [],
    }])
    ws = MathWorkspace(problem_spec=spec, subproblem_id="problem1", evidence_profile={"evidence": []},
                       registry=build_default_operator_registry())
    schema = ws.execute("inspect_model", {"schema": "parameters"})["schema"]
    fields = schema["$defs"]["StaticIndexedParameterDraft"]["properties"]
    assert "binding" in fields and "dense_values" not in fields
    binding = fields["binding"]["discriminator"]["mapping"]
    assert set(binding) == {"fact", "table_column", "table_range"}
