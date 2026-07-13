import asyncio
import json
from pathlib import Path


class IRModeler:
    async def model(self, **kwargs):
        from app.guidance.model_ir import ModelIR
        from tests.test_model_ir_contracts import _solvable_payload

        return ModelIR(
            schema_version="1.0",
            model_id="stage_boundary_model",
            subproblems=[_solvable_payload()],
        )

    async def revise(self, **kwargs):
        raise AssertionError("revision is not part of this initial stage contract test")


class IRReviewer:
    def __init__(self) -> None:
        self.received_model_ir = None

    async def review(self, *, problem_spec, model_ir, task, work_dir):
        from app.guidance.contracts import ModelReview
        from app.guidance.model_ir import ModelIR

        assert isinstance(model_ir, ModelIR)
        self.received_model_ir = model_ir
        return ModelReview(
            model_id=model_ir.model_id,
            status="approved",
            identifiability="checked from typed expressions",
            dimensional_consistency="checked from variable and data units",
            data_sufficiency="sufficient for the selected operators",
            computational_feasibility="registered operators required",
        )


def test_modeling_and_review_stages_exchange_only_model_ir_files(tmp_path: Path) -> None:
    from app.guidance.model_ir import ModelIR
    from app.guidance.stages import ModelingStage, ModelReviewStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({
            "task_summary": "Estimate a constrained aggregate model.",
            "subproblems": [{"id": "problem1"}],
        }),
        encoding="utf-8",
    )
    model_ir_path = asyncio.run(ModelingStage(modeler=IRModeler()).run(
        task_id="model-ir-stage",
        task={"work_dir": str(tmp_path)},
        input_path=problem_path,
        output_path=tmp_path / "model_ir.json",
    ))

    model_ir = ModelIR.model_validate_json(model_ir_path.read_text(encoding="utf-8"))
    assert model_ir.model_id == "stage_boundary_model"
    assert not (tmp_path / "model_spec.json").exists()

    reviewer = IRReviewer()
    approved_path = asyncio.run(ModelReviewStage(reviewer=reviewer).run(
        task_id="model-ir-review",
        task={"work_dir": str(tmp_path)},
        input_path=model_ir_path,
        output_path=tmp_path / "approved_model_ir.json",
    ))

    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    assert approved["review_status"] == "approved"
    assert approved["model_ir_ref"] == "model_ir.json"
    assert approved["approved_model_ir"]["model_id"] == "stage_boundary_model"
    assert "approved_model" not in approved
    assert reviewer.received_model_ir == model_ir
