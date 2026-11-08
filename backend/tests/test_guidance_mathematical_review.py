import asyncio
import json
from pathlib import Path

import pytest

from app.guidance.contracts import VerificationReport
from app.guidance.model_review_contracts import ModelIRReview, ModelReviewSubmission
from app.guidance.stages import ModelReviewStage
from app.guidance.verification_stage import ResultVerificationStage
from test_guidance_mathematical_adequacy import _execute_case


def _review_payload(model) -> dict:
    subproblem = model.subproblems[0]
    prefix = "/execution_components/0/model"
    checks = [
        ("stock_balance", [f"{prefix}/constraints/3", f"{prefix}/constraints/4"],
         "Initial inventory is five minus demand, loss and maintenance; later inventory adds commissioned purchases and deducts those outflows."),
        ("delivery_delay", [f"{prefix}/constraints/6"],
         "Commissioned units in t equal purchases in t-1, with no initial commissioned units."),
        ("setup_activity", [f"{prefix}/constraints/9"],
         "purchase[t] <= 20 * setup[t], and binary setup is charged in the objective."),
        ("$objective", [f"{prefix}/objective"],
         "The objective sums acquisition tiers, setup activities and remaining inventory costs over every period."),
        ("$formulation", [f"{prefix}/variables/0", f"{prefix}/constraints/3", f"{prefix}/constraints/4"],
         "Integer purchases and inventories, initial conditions and transitions account for all stock; the horizon and one-period delivery are explicit."),
    ]
    return {
        "model_id": model.model_id,
        "assumption_checks": [],
        "subproblem_reviews": [{
            "subproblem_id": subproblem.subproblem_id,
            "status": "approved",
            "mathematical_checks": [{
                "requirement_id": requirement,
                "status": "passed",
                "model_paths": paths,
                "rationale": rationale,
            } for requirement, paths, rationale in checks],
        }],
    }


class CorrespondenceReviewer:
    def __init__(self, alter=None):
        self.alter = alter

    async def review(self, *, model_ir, **kwargs):
        payload = _review_payload(model_ir)
        if self.alter:
            self.alter(payload)
        return ModelIRReview.model_validate(payload)


def _review_case(root: Path, alter=None) -> VerificationReport:
    _execute_case(root, "original")
    from app.artifacts.input_catalog import build_input_catalog

    source = root / "question.txt"
    text = "Conserve stock from five initial items. Purchases arrive one period later and require a paid setup activity."
    source.write_text(text, encoding="utf-8")
    build_input_catalog(source, work_dir=root, extracted_text=text)
    submission = root / "model_review_submission.json"
    submission.write_text(
        ModelReviewSubmission(model_ir_ref="model_ir.json").model_dump_json(), encoding="utf-8",
    )
    asyncio.run(ModelReviewStage(reviewer=CorrespondenceReviewer(alter)).run(
        task_id="mathematical-review", task={}, input_path=submission,
        output_path=root / "approved_model_ir.json",
    ))
    from app.guidance.model_ir_compiler import ModelIRCompiler
    from app.guidance.operator_catalog import build_default_operator_registry
    from app.guidance.operator_execution import OperatorGraphExecutor
    from app.guidance.model_ir import ModelIR

    registry = build_default_operator_registry()
    model = ModelIR.model_validate_json((root / "model_ir.json").read_text(encoding="utf-8"))
    compilation = ModelIRCompiler(registry=registry).compile_to_file(model, output_path=root / "execution_plan.json")
    OperatorGraphExecutor(registry=registry).execute(
        execution_plan=compilation.execution_plan, model_ir=model, work_dir=root,
    )
    return _verify(root)


def _verify(root: Path) -> VerificationReport:
    output = asyncio.run(ResultVerificationStage().run(
        task_id="mathematical-review", task={},
        input_path=root / "execution_manifest.json", output_path=root / "verification_report.json",
    ))
    return VerificationReport.model_validate_json(output.read_text(encoding="utf-8"))


def test_independent_correspondence_survives_review_stage_and_verification(tmp_path: Path) -> None:
    report = _review_case(tmp_path)
    assert report.model_adequate.mathematical_review.status == "passed"
    assert report.model_adequate.numerical_validation.status == "passed"
    assert report.status == "verified"
    assert any("model_review.json" in ref for ref in report.model_adequate.mathematical_review.evidence_refs)


@pytest.mark.parametrize("artifact", ["model_ir.json", "approved_model_ir.json", "problem_spec.json", "model_review.json", "evidence_profile.json", "input_catalog.json"])
def test_review_does_not_survive_changes_to_its_evidence_snapshot(tmp_path: Path, artifact: str) -> None:
    _review_case(tmp_path)
    path = tmp_path / artifact
    document = json.loads(path.read_text(encoding="utf-8"))
    if artifact == "model_ir.json":
        document["subproblems"][0]["objective"] += " Changed objective."
    elif artifact == "approved_model_ir.json":
        document["approved_model_ir"]["subproblems"][0]["objective"] += " Changed projection."
    elif artifact == "problem_spec.json":
        document["locked_facts"][0]["value"] = 6
    elif artifact == "model_review.json":
        document["subproblem_reviews"][0]["mathematical_checks"][0]["rationale"] += " Changed claim."
    elif artifact == "evidence_profile.json":
        document["evidence"] = [{
            "evidence_id": "changed", "kind": "uploaded_file", "source_ref": "data/new.txt",
            "source_file": "data/new.txt", "sha256": "0" * 64, "size_bytes": 1,
        }]
    else:
        document["blocks"][0]["text"] += " Initial stock changed to six."
    path.write_text(json.dumps(document), encoding="utf-8")
    report = _verify(tmp_path)
    assert report.model_adequate.mathematical_review.status == "not_assessed"
    assert report.status != "verified"


def test_missing_correspondence_is_unassessed_even_with_review_binding(tmp_path: Path) -> None:
    report = _review_case(tmp_path, lambda data: data["subproblem_reviews"][0]["mathematical_checks"].pop())
    assert report.model_adequate.mathematical_review.status == "not_assessed"
    assert report.status == "partial"


def test_missing_source_snapshot_is_not_silently_rebuilt_during_verification(tmp_path: Path) -> None:
    _review_case(tmp_path)
    (tmp_path / "evidence_profile.json").unlink()
    report = _verify(tmp_path)
    assert report.model_adequate.mathematical_review.status == "not_assessed"
    assert not (tmp_path / "evidence_profile.json").exists()


def test_json_formatting_does_not_invalidate_review_snapshot(tmp_path: Path) -> None:
    _review_case(tmp_path)
    path = tmp_path / "model_ir.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(document, sort_keys=True, indent=4), encoding="utf-8")
    assert _verify(tmp_path).model_adequate.mathematical_review.status == "passed"


@pytest.mark.parametrize("defect", ["failed", "unknown_requirement", "missing_path", "cross_subproblem", "duplicate"])
def test_invalid_or_rejected_correspondence_cannot_approve_execution(tmp_path: Path, defect: str) -> None:
    def alter(data):
        checks = data["subproblem_reviews"][0]["mathematical_checks"]
        if defect == "failed":
            checks[0]["status"] = "failed"
        elif defect == "unknown_requirement":
            checks[0]["requirement_id"] = "another_subproblem_fact"
        elif defect == "missing_path":
            checks[0]["model_paths"] = ["/execution_components/0/model/constraints/999"]
        elif defect == "cross_subproblem":
            checks[0]["model_paths"] = ["/subproblems/1/execution_components/0"]
        else:
            checks.append(checks[0].copy())

    with pytest.raises(ValueError, match="mathematical"):
        _review_case(tmp_path, alter)
