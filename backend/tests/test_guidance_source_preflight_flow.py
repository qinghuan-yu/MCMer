"""来源前置在流水线、建模入口和恢复中的强制门禁。"""

import asyncio
import json

import pytest

from app.guidance.contracts import ProblemSpec
from app.guidance.model_ir import ModelIR
from app.guidance.pipeline import GuidancePipeline
from app.guidance.recovery import RecoveryCheckpoint, model_source_snapshots
from app.guidance.stages import ModelingStage
from test_guidance_pipeline_ir import RecordingStage, _approved_payload, _blocked_ir, _run_pipeline


def _spec():
    return ProblemSpec(
        task_summary="Interpret the registered source before modeling.",
        subproblems=[{"id": "problem1", "objective": "Evaluate training cost.",
                      "required_fact_ids": [], "required_uncertainty_ids": ["UNC001"],
                      "depends_on_subproblem_ids": []}],
        uncertainties=[{"uncertainty_id": "UNC001", "statement": "Is a guide charged?",
                        "source_refs": ["inputs/question.txt"], "subproblem_ids": ["problem1"],
                        "semantic_key": "training.cost", "candidate_values": [8, 16], "unit": "yuan"}],
    )


def test_pipeline_runs_source_stage_before_any_model_generation(tmp_path):
    calls = []
    pipeline = GuidancePipeline(
        decomposition_stage=RecordingStage("decomposition", calls),
        source_stage=RecordingStage("source", calls),
        modeling_stage=RecordingStage("modeling", calls, _blocked_ir()),
        model_review_stage=RecordingStage("model_review", calls, _approved_payload()),
        calculation_stage=RecordingStage("calculation", calls),
        result_verification_stage=RecordingStage("result_verification", calls),
        guidance_stage=RecordingStage("guidance", calls),
    )
    _run_pipeline(pipeline, "source-first", tmp_path)
    assert [name for name, _ in calls][:3] == ["decomposition", "source", "modeling"]


def test_modeling_refuses_missing_complete_source_permission(tmp_path):
    spec = _spec()
    (tmp_path / "problem_spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
    (tmp_path / "evidence_profile.json").write_text('{"evidence": []}', encoding="utf-8")
    calls = []

    class Modeler:
        async def model(self, **kwargs):
            calls.append(kwargs)
            return ModelIR.model_validate(_blocked_ir())

    with pytest.raises(ValueError, match="source|preflight|permission"):
        asyncio.run(ModelingStage(modeler=Modeler()).run(
            task_id="missing-source-permission", task={"work_dir": str(tmp_path)},
            input_path=tmp_path / "problem_spec.json", output_path=tmp_path / "model_ir.json",
        ))
    assert calls == []


def test_model_recovery_dependencies_include_source_permission_bytes(tmp_path):
    model = ModelIR.model_validate(_blocked_ir())
    permit = tmp_path / "source_preflight.json"
    permit.write_text('{"binding": "original complete permission"}', encoding="utf-8")
    checkpoint = RecoveryCheckpoint(tmp_path, "models", "problem1")
    checkpoint.save({"same_model_input": True}, model.model_dump(mode="json"),
                    files=model_source_snapshots(tmp_path, model))
    permit.write_text('{"binding": "replaced permission"}', encoding="utf-8")
    assert checkpoint.load({"same_model_input": True}) is None


def test_old_approved_snapshot_cannot_reach_compiler_without_source_permission(tmp_path):
    from app.guidance.stages import CalculationStage

    (tmp_path / "problem_spec.json").write_text(_spec().model_dump_json(), encoding="utf-8")
    (tmp_path / "evidence_profile.json").write_text('{"evidence": []}', encoding="utf-8")
    approved_path = tmp_path / "approved_model_ir.json"
    approved_path.write_text(json.dumps(_approved_payload()), encoding="utf-8")

    class Compiler:
        def compile_to_file(self, *args, **kwargs):
            raise AssertionError("old snapshot reached compiler without a source permit")

    with pytest.raises(ValueError, match="source|preflight|permission"):
        asyncio.run(CalculationStage(compiler=Compiler(), executor=object()).run(
            task_id="old-source-snapshot", task={}, input_path=approved_path,
            output_path=tmp_path / "execution_manifest.json",
        ))
