import asyncio
import json
from pathlib import Path

import pytest


class FixtureDecomposer:
    async def decompose(self, *, question: str, data_files: list[str], task: dict):
        from app.guidance.contracts import ProblemSpec

        return ProblemSpec(task_summary=question, subproblems=[{"id": "problem_1"}])


class UnchangedInvalidModeler:
    async def model(self, *, problem_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelSpec

        return ModelSpec(
            model_id="invalid-model",
            selected_method="candidate model under review",
            subproblem_models=[{
                "subproblem_id": "problem_1",
                "objective": "Fit the candidate model.",
                "selected_method": "candidate model under review",
                "rationale": "Fixture model for independent review.",
                "equations": ["y = f(x)"],
                "parameters": [{
                    "parameter_id": "theta",
                    "symbol": "theta",
                    "meaning": "candidate parameter",
                    "unit": "fixture unit",
                    "source_type": "estimated",
                    "source_detail": "fixture data",
                    "estimation_method": "fixture estimator",
                    "constraints": ["fixture constraint"],
                }],
                "constraints": ["fixture constraint"],
                "solve_steps": ["Run the fixture estimator."],
                "expected_results": ["fixture result"],
            }],
        )

    async def revise(
        self,
        *,
        problem_spec,
        previous_model,
        review,
        task: dict,
        work_dir: Path,
    ):
        from app.guidance.contracts import RevisedModelSpec

        return RevisedModelSpec.model_validate(
            {
                **previous_model.model_dump(mode="json"),
                "review_resolutions": [
                    {
                        "requirement": requirement,
                        "resolution": "Fixture declares the requested change for independent re-review.",
                        "affected_subproblem_ids": ["problem_1"],
                        "changed_fields": ["subproblem_models.0"],
                    }
                    for requirement in review.required_revisions
                ],
            }
        )


class FixtureReviewer:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture
        self.calls = 0

    async def review(self, *, problem_spec, model_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelReview

        self.calls += 1
        fields = {
            "identifiability": "passed",
            "dimensional_consistency": "passed",
            "data_sufficiency": "passed",
            "computational_feasibility": "passed",
        }
        fields[self.fixture["review_field"]] = self.fixture["review_value"]
        return ModelReview(
            model_id=model_spec.model_id,
            status="revision_required",
            blocking_issues=[self.fixture["issue"]],
            required_revisions=[self.fixture["required_revision"]],
            **fields,
        )


class NeverRunStage:
    async def run(self, **kwargs):
        raise AssertionError("calculation and verification must not run for rejected models")


@pytest.mark.parametrize(
    "fixture_name",
    ["unidentifiable.json", "wrong_unit.json", "insufficient_data.json"],
)
def test_review_fixture_blocks_before_solver_generation(
    tmp_path: Path,
    fixture_name: str,
) -> None:
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.stages import (
        DecompositionStage,
        GuidanceStage,
        ModelingStage,
        ModelReviewStage,
    )

    fixture_path = Path(__file__).parent / "fixtures" / "guidance_review" / fixture_name
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    reviewer = FixtureReviewer(fixture)
    pipeline = GuidancePipeline(
        decomposition_stage=DecompositionStage(decomposer=FixtureDecomposer()),
        modeling_stage=ModelingStage(modeler=UnchangedInvalidModeler()),
        model_review_stage=ModelReviewStage(reviewer=reviewer),
        calculation_stage=NeverRunStage(),
        result_verification_stage=NeverRunStage(),
        guidance_stage=GuidanceStage(),
    )
    task = {
        "task_id": f"review-{fixture['case_id']}",
        "question": "Review an intentionally invalid modeling specification.",
        "work_dir": str(tmp_path),
    }

    async def collect():
        return [message async for message in pipeline.run(task["task_id"], task)]

    messages = asyncio.run(collect())

    review = json.loads((tmp_path / "model_review.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    guidance = (tmp_path / "guidance.md").read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))
    assert reviewer.calls == 2
    assert review[fixture["review_field"]] == fixture["review_value"]
    assert report["status"] == "blocked"
    assert not (tmp_path / "solve.py").exists()
    assert not (tmp_path / "execution_manifest.json").exists()
    assert fixture["issue"] in guidance
    assert fixture["required_revision"] in guidance
    assert audit["status"] == "PASS"
    assert messages[-1]["data"]["status"] == "blocked"
