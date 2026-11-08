from __future__ import annotations

import importlib
import asyncio
from pathlib import Path
from types import SimpleNamespace
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


def test_indexed_core_request_requires_exact_static_parameter_source(monkeypatch) -> None:
    from app.guidance import model_generation
    from app.guidance.contracts import ProblemSpec
    from app.guidance.model_ir_drafts import ModelIRGenerationDraft, SolvableModelIRDraft

    captured = {}
    architecture = SolvableModelIRDraft.model_construct(
        execution_components=["indexed_linear_model"], operators=[],
    )

    async def capture_request(**kwargs):
        if kwargs["model_type"] is ModelIRGenerationDraft:
            return SimpleNamespace(
                subproblems=[architecture], model_dump=lambda **_: {},
            )
        captured["prompt"] = kwargs["system_prompt"]
        raise RuntimeError("captured core request")

    monkeypatch.setattr(model_generation, "_request_model", capture_request)
    with pytest.raises(RuntimeError, match="captured core request"):
        asyncio.run(model_generation._request_single_subproblem_model(
            llm=None,
            system_prompt="Build the requested model.",
            problem_spec=ProblemSpec(
                task_summary="A finite indexed optimization.",
                subproblems=[{
                    "id": "problem1", "required_fact_ids": [],
                    "required_uncertainty_ids": [], "depends_on_subproblem_ids": [],
                }],
            ),
            compact_spec={"task_summary": "A finite indexed optimization."},
            subproblem={"id": "problem1"},
            slice_spec={"locked_facts": [], "uncertainties": []},
            evidence_profile={"evidence": []},
            available_operator_ids=["optimization.milp"],
            request_name="test_source_contract",
            artifact_dir=Path("."),
            previous_model=None,
            review=None,
            architecture_timeout=None,
            indexed_core_timeout=None,
            indexed_constraints_timeout=None,
        ))

    for rule in (
        "完整已登记表格 source_ref",
        "精确列名",
        "单值 cell/ref",
        "标量 locked fact",
        "禁止用向量首个单元格作为整列来源",
    ):
        assert rule in captured["prompt"]
