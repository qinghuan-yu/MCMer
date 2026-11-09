import asyncio
import json
from pathlib import Path


class IRModeler:
    async def model(self, **kwargs):
        from app.guidance.model_ir import BlockedModelIR, ModelIR

        return ModelIR(
            schema_version="1.0",
            model_id="stage_boundary_model",
            subproblems=[
                BlockedModelIR(
                    execution_status="blocked",
                    subproblem_id="problem1",
                    objective="Estimate a constrained aggregate model.",
                    blocking_reason="The compiler operator is not registered yet.",
                    missing_inputs=["registered compiler operator"],
                    recovery_action="Register the operator before compiling this Model IR.",
                    expected_outputs=["compiled aggregate model"],
                )
            ],
        )

    async def revise(self, **kwargs):
        raise AssertionError("revision is not part of this initial stage contract test")


class IRReviewer:
    def __init__(self) -> None:
        self.received_model_ir = None
        self.received_revision_request = None

    async def review(
        self,
        *,
        problem_spec,
        model_ir,
        revision_request,
        evidence_profile,
        input_catalog,
        task,
        work_dir,
    ):
        from app.guidance.model_ir import (ModelIR)
        from app.guidance.model_review_contracts import (ModelIRReview)

        assert isinstance(model_ir, ModelIR)
        self.received_model_ir = model_ir
        self.received_revision_request = revision_request
        requested_revisions = (
            revision_request.subproblem_reviews[0].required_revisions
            if revision_request is not None
            else []
        )
        revision_checks = [
            {
                "requirement_id": item.requirement_id,
                "status": "accepted",
                "model_paths": ["/recovery_action"],
                "rationale": "The revised ModelIR exposes the requested recovery action.",
            }
            for item in requested_revisions
        ]
        return ModelIRReview(
            model_id=model_ir.model_id,
            identifiability="checked from typed expressions",
            dimensional_consistency="checked from variable and data units",
            data_sufficiency="sufficient for the selected operators",
            computational_feasibility="registered operators required",
            subproblem_reviews=[{
                "subproblem_id": "problem1",
                "status": "blocked",
                "assumption_checks": [],
                "blocking_issues": [
                    "The compiler operator is not registered yet."
                ],
                "revision_checks": revision_checks,
            }],
        )


class RevisingIRModeler(IRModeler):
    def __init__(self) -> None:
        self.previous_model_ir = None

    async def revise(self, *, previous_model_ir, review, **kwargs):
        self.previous_model_ir = previous_model_ir
        return previous_model_ir


def test_modeling_and_review_stages_exchange_only_model_ir_files(tmp_path: Path) -> None:
    from app.guidance.model_ir import (ModelIR)
    from app.guidance.model_review_contracts import (ModelReviewSubmission)
    from app.guidance.stages import ModelingStage, ModelReviewStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({
            "task_summary": "Estimate a constrained aggregate model.",
            "subproblems": [{
                "id": "problem1",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            }],
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
    submission_path = tmp_path / "model_review_submission.json"
    submission_path.write_text(
        ModelReviewSubmission(model_ir_ref=model_ir_path.name).model_dump_json(indent=2),
        encoding="utf-8",
    )
    approved_path = asyncio.run(ModelReviewStage(reviewer=reviewer).run(
        task_id="model-ir-review",
        task={"work_dir": str(tmp_path)},
        input_path=submission_path,
        output_path=tmp_path / "approved_model_ir.json",
    ))

    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    assert approved["review_status"] == "blocked"
    assert approved["executable_subproblem_ids"] == []
    assert approved["model_ir_ref"] == "model_ir.json"
    assert approved["approved_model_ir"]["model_id"] == "stage_boundary_model"
    assert "approved_model" not in approved
    assert reviewer.received_model_ir == model_ir
    assert reviewer.received_revision_request is None


def test_model_review_stage_passes_explicit_revision_request(tmp_path: Path) -> None:
    from app.guidance.model_review_contracts import (ModelIRReview, ModelReviewSubmission)
    from app.guidance.stages import ModelingStage, ModelReviewStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({
            "task_summary": "Estimate a constrained aggregate model.",
            "subproblems": [{
                "id": "problem1",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            }],
        }),
        encoding="utf-8",
    )
    model_ir_path = asyncio.run(ModelingStage(modeler=IRModeler()).run(
        task_id="model-ir-initial",
        task={"work_dir": str(tmp_path)},
        input_path=problem_path,
        output_path=tmp_path / "model_ir.json",
    ))
    revision_request = ModelIRReview(
        model_id="stage_boundary_model",
        subproblem_reviews=[{
            "subproblem_id": "problem1",
            "status": "revision_required",
            "assumption_checks": [],
            "required_revisions": [{
                "requirement_id": "register-compiler-operator",
                "description": "Register a supported compiler operator.",
                "acceptance_criteria": "The revised ModelIR uses a registered operator.",
            }],
        }],
    )
    revision_path = tmp_path / "revision_request.json"
    revision_path.write_text(revision_request.model_dump_json(indent=2), encoding="utf-8")
    submission_path = tmp_path / "model_review_submission.json"
    submission_path.write_text(
        ModelReviewSubmission(
            model_ir_ref=model_ir_path.name,
            revision_request_ref=revision_path.name,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    reviewer = IRReviewer()

    asyncio.run(ModelReviewStage(reviewer=reviewer).run(
        task_id="model-ir-rereview",
        task={"work_dir": str(tmp_path)},
        input_path=submission_path,
        output_path=tmp_path / "approved_model_ir.json",
    ))

    assert reviewer.received_revision_request == revision_request


def test_modeling_stage_revision_reads_model_ir_review_without_model_spec_aliases(
    tmp_path: Path,
) -> None:
    from app.guidance.model_review_contracts import (ModelIRReview)
    from app.guidance.stages import ModelingStage

    problem_path = tmp_path / "problem_spec.json"
    problem_path.write_text(
        json.dumps({
            "task_summary": "Estimate a constrained aggregate model.",
            "subproblems": [{
                "id": "problem1",
                "required_fact_ids": [],
                "required_uncertainty_ids": [],
                "depends_on_subproblem_ids": [],
            }],
        }),
        encoding="utf-8",
    )
    model_ir_path = asyncio.run(ModelingStage(modeler=IRModeler()).run(
        task_id="model-ir-initial",
        task={"work_dir": str(tmp_path)},
        input_path=problem_path,
        output_path=tmp_path / "model_ir.json",
    ))
    review_path = tmp_path / "model_review.json"
    review_path.write_text(
        ModelIRReview(
            model_id="stage_boundary_model",
            model_ir_ref=model_ir_path.name,
            subproblem_reviews=[{
                "subproblem_id": "problem1",
                "status": "revision_required",
                "assumption_checks": [],
                "required_revisions": [{
                    "requirement_id": "register-compiler-operator",
                    "description": "Register a supported compiler operator.",
                    "acceptance_criteria": "The revised ModelIR uses a registered operator.",
                }],
            }],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    modeler = RevisingIRModeler()

    revised_path = asyncio.run(ModelingStage(modeler=modeler).run(
        task_id="model-ir-revision",
        task={"work_dir": str(tmp_path)},
        input_path=review_path,
        output_path=tmp_path / "model_ir.json",
    ))

    assert revised_path.name == "model_ir.json"
    assert modeler.previous_model_ir is not None
    assert modeler.previous_model_ir.model_id == "stage_boundary_model"
    assert not (tmp_path / "model_spec.json").exists()
