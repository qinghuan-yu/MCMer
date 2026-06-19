import asyncio
import json
from pathlib import Path

from openpyxl import Workbook


class WuyiProblemOneDecomposer:
    async def decompose(self, *, question: str, data_files: list[str], task: dict):
        from app.guidance.contracts import ProblemSpec

        return ProblemSpec(
            task_summary=question,
            subproblems=[{
                "id": "problem_1",
                "objective": "Infer two branch-flow functions from the observed main-road flow.",
                "problem_type": "constrained piecewise regression",
            }],
            data_sources=[{"path": data_files[0], "table": "Table 1"}],
            locked_facts=[
                "The main-road flow is the sum of branch 1 and branch 2.",
                "Branch 1 increases linearly; branch 2 increases then decreases linearly.",
            ],
            uncertainties=["The branch decomposition is not unique without an identification constraint."],
        )


class WuyiProblemOneModeler:
    async def model(self, *, problem_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelSpec

        return ModelSpec(
            model_id="wuyi-2025-a-problem-1",
            selected_method="continuous piecewise least squares with a symmetric-slope identification constraint",
            candidate_methods=[
                {"name": "continuous piecewise least squares", "selected": True},
                {"name": "unconstrained branch decomposition", "selected": False, "reason": "not identifiable"},
            ],
            assumptions=[
                "The absolute increase and decrease slopes of branch 2 are equal; this explicit constraint identifies the branch split."
            ],
            symbols=[
                {"symbol": "t", "meaning": "two-minute sample index"},
                {"symbol": "q_1", "meaning": "branch 1 flow"},
                {"symbol": "q_2", "meaning": "branch 2 flow"},
            ],
            equations=[
                "q_main(t) = q_1(t) + q_2(t)",
                "q_1(t) = b_1 + a_1 t",
                "q_2(t) has equal-magnitude positive and negative slopes around t_break",
            ],
            parameters=[
                {"symbol": "t_break", "source": "least-squares breakpoint scan"},
                {"symbol": "a_1", "source": "identified from aggregate segment slopes"},
                {"symbol": "a_2", "source": "identified from aggregate segment slopes"},
            ],
            constraints=["q_1(t) >= 0", "q_2(t) >= 0", "q_2 is continuous at t_break"],
            solve_steps=[
                "Read Table 1 from the XLSX attachment.",
                "Scan continuous piecewise-linear breakpoints and minimize squared residuals.",
                "Apply the symmetric-slope identification constraint to recover both branches.",
                "Recompose the main-road flow and calculate residual metrics.",
            ],
            required_outputs=[
                "solver_results.json",
                "parameter_registry.json",
                "result_registry.json",
                "parameter_table.csv",
                "figures/problem1_fit.png",
            ],
            figure_requests=[{
                "path": "figures/problem1_fit.png",
                "role": "result_fit",
                "linked_result_ids": ["result_problem1_piecewise_fit"],
                "source_data": ["data/attachment.xlsx"],
            }],
        )


class WuyiProblemOneReviewer:
    async def review(self, *, model_spec, task: dict, work_dir: Path):
        from app.guidance.contracts import ModelReview

        return ModelReview(
            model_id=model_spec.model_id,
            status="approved",
            identifiability="conditional_on_symmetric_branch2_slopes",
            dimensional_consistency="passed",
            data_sufficiency="passed_for_piecewise_fit",
            computational_feasibility="passed",
        )


class WuyiProblemOneProgramGenerator:
    async def generate(self, *, approved_model, task: dict, work_dir: Path):
        from app.guidance.contracts import GeneratedProgram

        return GeneratedProgram(source_code=WUYI_PROBLEM_ONE_SOLVER)


WUYI_PROBLEM_ONE_SOLVER = r'''
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

source = Path("data/attachment.xlsx")
frame = pd.read_excel(source, sheet_name=0)
t = frame.iloc[:, 1].astype(float).to_numpy()
y = frame.iloc[:, 2].astype(float).to_numpy()

best = None
for breakpoint in range(2, len(t) - 2):
    design = np.column_stack([np.ones_like(t), t, np.maximum(0.0, t - breakpoint)])
    coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coefficients
    sse = float(np.sum((y - fitted) ** 2))
    if best is None or sse < best[0]:
        best = (sse, breakpoint, coefficients, fitted)

sse, t_break, coefficients, fitted = best
intercept = float(coefficients[0])
slope_before = float(coefficients[1])
slope_after = float(coefficients[1] + coefficients[2])
branch1_slope = (slope_before + slope_after) / 2.0
branch2_slope = (slope_before - slope_after) / 2.0
branch1 = intercept + branch1_slope * t
branch2 = np.where(
    t <= t_break,
    branch2_slope * t,
    branch2_slope * (2.0 * t_break - t),
)
recomposed = branch1 + branch2
residual = y - recomposed
rmse = float(np.sqrt(np.mean(residual ** 2)))
max_abs_residual = float(np.max(np.abs(residual)))

parameters = [
    {"id": "param_t_break", "symbol": "t_break", "name": "breakpoint", "value": int(t_break), "unit": "sample index", "source_type": "estimated_from_data", "source_ref": str(source), "trust_status": "estimated"},
    {"id": "param_branch1_intercept", "symbol": "b_1", "name": "branch 1 intercept", "value": intercept, "unit": "flow", "source_type": "estimated_from_data", "source_ref": str(source), "trust_status": "estimated"},
    {"id": "param_branch1_slope", "symbol": "a_1", "name": "branch 1 slope", "value": branch1_slope, "unit": "flow/sample", "source_type": "estimated_from_data", "source_ref": str(source), "trust_status": "estimated"},
    {"id": "param_branch2_slope", "symbol": "a_2", "name": "branch 2 slope magnitude", "value": branch2_slope, "unit": "flow/sample", "source_type": "estimated_from_data", "source_ref": str(source), "trust_status": "estimated"},
]
result = {
    "id": "result_problem1_piecewise_fit",
    "claim_text": "The constrained branch-flow model recomposes the observed main-road flow.",
    "metrics": {"rmse": rmse, "max_abs_residual": max_abs_residual},
    "source_data": [str(source)],
    "linked_parameter_ids": [item["id"] for item in parameters],
}

Path("parameter_registry.json").write_text(json.dumps({"parameters": parameters}, ensure_ascii=False, indent=2), encoding="utf-8")
Path("result_registry.json").write_text(json.dumps({"verified_results": [result], "blocked_results": [], "summary": {"coverage_status": "verified"}}, ensure_ascii=False, indent=2), encoding="utf-8")
Path("solver_results.json").write_text(json.dumps({"t": t.tolist(), "observed": y.tolist(), "branch1": branch1.tolist(), "branch2": branch2.tolist(), "recomposed": recomposed.tolist(), "residual": residual.tolist()}, ensure_ascii=False), encoding="utf-8")
pd.DataFrame(parameters).to_csv("parameter_table.csv", index=False)

Path("figures").mkdir(exist_ok=True)
fig, ax = plt.subplots(figsize=(8, 5))
ax.plot(t, y, "o", markersize=3, label="Observed main road")
ax.plot(t, recomposed, "-", label="Recomposed flow")
ax.plot(t, branch1, "--", label="Branch 1")
ax.plot(t, branch2, "--", label="Branch 2")
ax.set_xlabel("Sample index")
ax.set_ylabel("Traffic flow")
ax.legend()
fig.tight_layout()
fig.savefig("figures/problem1_fit.png", dpi=160)
plt.close(fig)
print("complete solver finished")
'''.strip()


def _write_problem_one_xlsx(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Table 1"
    sheet.append(["Moment", "Time t", "Main road 3 flow"])
    for t in range(60):
        flow = 7.0 + 1.5 * t if t <= 30 else 67.0 - 0.5 * t
        sheet.append([f"sample-{t}", t, flow])
    workbook.save(path)


def test_wuyi_problem_one_runs_real_xlsx_jupyter_and_png_end_to_end(tmp_path: Path) -> None:
    from app.guidance.execution import LocalProgramExecutor
    from app.guidance.pipeline import GuidancePipeline
    from app.guidance.stages import (
        CalculationStage,
        DecompositionStage,
        GuidanceStage,
        ModelingStage,
        ModelReviewStage,
        ResultVerificationStage,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_problem_one_xlsx(data_dir / "attachment.xlsx")
    pipeline = GuidancePipeline(
        decomposition_stage=DecompositionStage(decomposer=WuyiProblemOneDecomposer()),
        modeling_stage=ModelingStage(modeler=WuyiProblemOneModeler()),
        model_review_stage=ModelReviewStage(reviewer=WuyiProblemOneReviewer()),
        calculation_stage=CalculationStage(
            program_generator=WuyiProblemOneProgramGenerator(),
            executor=LocalProgramExecutor(),
        ),
        result_verification_stage=ResultVerificationStage(),
        guidance_stage=GuidanceStage(),
    )
    task = {
        "task_id": "wuyi-problem-one",
        "question": "Infer branch 1 and branch 2 traffic-flow functions for Problem 1.",
        "work_dir": str(tmp_path),
    }

    async def collect():
        return [message async for message in pipeline.run("wuyi-problem-one", task)]

    messages = asyncio.run(collect())

    parameters = json.loads((tmp_path / "parameter_registry.json").read_text(encoding="utf-8"))
    parameter_values = {item["id"]: item["value"] for item in parameters["parameters"]}
    results = json.loads((tmp_path / "result_registry.json").read_text(encoding="utf-8"))
    verification = json.loads((tmp_path / "verification_report.json").read_text(encoding="utf-8"))
    guidance = (tmp_path / "guidance.md").read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "guidance_audit_report.json").read_text(encoding="utf-8"))

    assert parameter_values["param_t_break"] == 30
    assert abs(parameter_values["param_branch1_slope"] - 0.5) < 1e-10
    assert abs(parameter_values["param_branch2_slope"] - 1.0) < 1e-10
    assert results["verified_results"][0]["metrics"]["rmse"] < 1e-10
    assert verification["status"] == "verified"
    assert (tmp_path / "execution_manifest.json").is_file()
    assert json.loads((tmp_path / "execution_manifest.json").read_text(encoding="utf-8"))["execution_count"] == 1
    assert (tmp_path / "notebook.ipynb").is_file()
    assert (tmp_path / "figures" / "problem1_fit.png").stat().st_size > 1000
    assert "param_branch1_slope" in guidance
    assert "result_problem1_piecewise_fit" in guidance
    assert "可辨识" in guidance
    assert audit["status"] == "PASS"
    assert messages[-1]["data"]["status"] == "completed"
