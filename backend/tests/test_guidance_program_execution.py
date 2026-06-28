import asyncio
import json
from pathlib import Path

import pytest


class FakeInterpreter:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.execute_calls = 0
        self.closed = False

    async def execute_code(self, code: str):
        self.execute_calls += 1
        (self.work_dir / "solver_results.json").write_text(
            json.dumps({"verified_results": [{"id": "result_fit", "value": 2.5}]}),
            encoding="utf-8",
        )
        (self.work_dir / "parameter_table.csv").write_text("symbol,value\na,2.5\n", encoding="utf-8")
        figures = self.work_dir / "figures"
        figures.mkdir(exist_ok=True)
        (figures / "fit.png").write_bytes(b"png")
        return "completed", False, ""

    async def save_notebook(self, filepath: str):
        Path(filepath).write_text("{}", encoding="utf-8")

    async def close(self):
        self.closed = True


class ThrowingInterpreter(FakeInterpreter):
    async def execute_code(self, code: str):
        self.execute_calls += 1
        raise RuntimeError("interpreter crashed before returning a result")


def test_program_executor_runs_complete_solver_once_and_records_artifacts(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    interpreter = FakeInterpreter(tmp_path)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="piecewise_flow",
        required_outputs=["solver_results.json", "parameter_table.csv", "figures/fit.png"],
    )
    program = GeneratedProgram(
        language="python",
        entrypoint="solve.py",
        source_code="print('run complete')",
        declared_outputs=approved_model.required_outputs,
    )

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-1",
            work_dir=tmp_path,
            approved_model=approved_model,
            program=program,
        )

    manifest = asyncio.run(run())

    assert interpreter.execute_calls == 1
    assert interpreter.closed is True
    assert (tmp_path / "solve.py").read_text(encoding="utf-8") == program.source_code
    assert (tmp_path / "notebook.ipynb").is_file()
    assert manifest.status == "passed"
    assert manifest.generated_files == [
        "solver_results.json",
        "parameter_table.csv",
        "figures/fit.png",
        "notebook.ipynb",
    ]
    saved = json.loads((tmp_path / "execution_manifest.json").read_text(encoding="utf-8"))
    assert saved["execution_count"] == 1
    assert saved["program_sha256"]


def test_program_executor_expands_required_output_glob(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    interpreter = FakeInterpreter(tmp_path)

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-glob",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="glob-output",
                required_outputs=["solver_results.json", "parameter_table.csv", "figures/*.png"],
            ),
            program=GeneratedProgram(
                source_code="print('run complete')",
                declared_outputs=["solver_results.json", "parameter_table.csv", "figures/*.png"],
            ),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "passed"
    assert "figures/fit.png" in manifest.generated_files
    assert manifest.missing_required_outputs == []


def test_program_executor_precreates_parent_dirs_for_required_outputs(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    class FigureOnlyInterpreter(FakeInterpreter):
        async def execute_code(self, code: str):
            self.execute_calls += 1
            assert (self.work_dir / "figures").is_dir()
            (self.work_dir / "figures" / "fit.png").write_bytes(b"png")
            return "figure written", False, ""

    interpreter = FigureOnlyInterpreter(tmp_path)

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-precreate-output-dirs",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="figure-output",
                required_outputs=["figures/fit.png"],
            ),
            program=GeneratedProgram(
                source_code="print('write figure')",
                declared_outputs=["figures/fit.png"],
            ),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "passed"
    assert manifest.generated_files == ["figures/fit.png", "notebook.ipynb"]


def test_program_executor_rejects_incomplete_declared_outputs_before_execution(
    tmp_path: Path,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    interpreter = FakeInterpreter(tmp_path)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="output-contract",
        required_outputs=[
            "solver_results.json",
            "parameter_registry.json",
            "result_registry.json",
            "figure_registry.json",
        ],
    )
    program = GeneratedProgram(
        source_code="print('incomplete outputs')",
        declared_outputs=["solver_results.json"],
    )

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-output-contract",
            work_dir=tmp_path,
            approved_model=approved_model,
            program=program,
        )

    with pytest.raises(ValueError, match="declared output mismatch"):
        asyncio.run(run())
    assert interpreter.execute_calls == 0


def test_program_executor_records_interpreter_exception_as_failed_manifest(
    tmp_path: Path,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    interpreter = ThrowingInterpreter(tmp_path)
    approved_model = ApprovedModelSpec(
        review_status="approved",
        model_id="runtime-failure",
        required_outputs=["solver_results.json"],
    )

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-runtime-failure",
            work_dir=tmp_path,
            approved_model=approved_model,
            program=GeneratedProgram(
                source_code="print('runtime failure')",
                declared_outputs=["solver_results.json"],
            ),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "failed"
    assert "interpreter crashed" in manifest.error_message
    assert json.loads((tmp_path / "execution_manifest.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_program_executor_rejects_unsafe_generated_code_before_execution(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor, UnsafeGeneratedProgramError

    interpreter = FakeInterpreter(tmp_path)
    program = GeneratedProgram(
        language="python",
        entrypoint="solve.py",
        source_code="import subprocess\nsubprocess.run(['cmd', '/c', 'echo unsafe'])",
        declared_outputs=["solver_results.json"],
    )

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-unsafe",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="unsafe",
                required_outputs=["solver_results.json"],
            ),
            program=program,
        )

    with pytest.raises(UnsafeGeneratedProgramError):
        asyncio.run(run())

    assert interpreter.execute_calls == 0


@pytest.mark.parametrize(
    ("source_code", "expected"),
    [
        (
            "import numpy as np\nvalues = np.random.normal(0, 1, 10)\nprint(values)",
            "np.random",
        ),
        (
            "from scipy.optimize import least_squares\n"
            "least_squares(lambda x: x, [1.0], constraints=[{'type': 'eq', 'fun': lambda x: x[0]}])",
            "least_squares",
        ),
        (
            "# 使用示例数据作为兜底\nprint('示例数据')",
            "示例",
        ),
        (
            "# not implemented due to complexity\nprint('blocked')",
            "not implemented",
        ),
        (
            "# would need to compute from model\nprint('empty evidence')",
            "would need to compute",
        ),
        (
            "constraint_values = []\nprint('bad regression evidence')",
            "constraint_values",
        ),
        (
            "evidence = {'constraint_values': []}\nprint(evidence)",
            "constraint_values",
        ),
        (
            "evidence = {'kind': 'regression', 'predicted': [], 'residuals': [0.0], 'constraint_values': [1.0]}\n"
            "print(evidence)",
            "predicted",
        ),
        (
            "evidence = {'kind': 'regression', 'predicted': [1.0], 'residuals': [], 'constraint_values': [1.0]}\n"
            "print(evidence)",
            "residuals",
        ),
        (
            "residuals = [1.0, -2.0]\n"
            "evidence = {'kind': 'regression', 'constraint_values': residuals}\n"
            "print(evidence)",
            "constraint_values.*residual",
        ),
    ],
)
def test_program_executor_rejects_untrustworthy_solver_patterns_before_execution(
    tmp_path: Path,
    source_code: str,
    expected: str,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor, UnsafeGeneratedProgramError

    interpreter = FakeInterpreter(tmp_path)

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-untrusted-solver",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="untrusted-solver",
                required_outputs=["solver_results.json"],
            ),
            program=GeneratedProgram(
                source_code=source_code,
                declared_outputs=["solver_results.json"],
            ),
        )

    with pytest.raises(UnsafeGeneratedProgramError, match=expected):
        asyncio.run(run())

    assert interpreter.execute_calls == 0


def test_program_executor_rejects_declared_root_outputs_written_under_subdirectory(
    tmp_path: Path,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor, UnsafeGeneratedProgramError

    interpreter = FakeInterpreter(tmp_path)
    source_code = """
import json

with open('results/parameter_registry.json', 'w') as f:
    json.dump({'parameters': [], 'summary': {}}, f)
with open('results/result_registry.json', 'w') as f:
    json.dump({'verified_results': [], 'blocked_results': [], 'summary': {}}, f)
with open('results/figure_registry.json', 'w') as f:
    json.dump({'figures': []}, f)
with open('results/solver_results.json', 'w') as f:
    json.dump({'evidence': []}, f)
""".strip()

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-misplaced-output",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="misplaced-output",
                required_outputs=[
                    "solver_results.json",
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
                ],
            ),
            program=GeneratedProgram(
                source_code=source_code,
                declared_outputs=[
                    "solver_results.json",
                    "parameter_registry.json",
                    "result_registry.json",
                    "figure_registry.json",
                ],
            ),
        )

    with pytest.raises(
        UnsafeGeneratedProgramError,
        match="solver_results.json|parameter_registry.json",
    ):
        asyncio.run(run())

    assert interpreter.execute_calls == 0


def test_program_executor_rejects_helper_function_with_undefined_global_before_execution(
    tmp_path: Path,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor, UnsafeGeneratedProgramError

    interpreter = FakeInterpreter(tmp_path)
    source_code = """
import numpy as np

def create_residual_plot(problem2_results, t):
    residuals = np.array(problem2_results['pred']) - np.array(Q5)
    return residuals

def main():
    Q5 = [1.0, 2.0, 3.0]
    create_residual_plot({'pred': [1.0, 2.0, 3.0]}, [0, 1, 2])

if __name__ == '__main__':
    main()
""".strip()

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-undefined-helper-global",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="undefined-helper-global",
                required_outputs=["solver_results.json"],
            ),
            program=GeneratedProgram(
                source_code=source_code,
                declared_outputs=["solver_results.json"],
            ),
        )

    with pytest.raises(UnsafeGeneratedProgramError, match="undefined global name.*Q5"):
        asyncio.run(run())

    assert interpreter.execute_calls == 0


def test_program_executor_allows_nested_helper_function_own_arguments(
    tmp_path: Path,
) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    interpreter = FakeInterpreter(tmp_path)
    source_code = """
def solve_problem1(values):
    def model(params, x):
        a, b = params
        return [a * item + b for item in x]
    fitted = model([1.0, 0.0], values)
    return fitted

solve_problem1([1.0, 2.0, 3.0])
""".strip()

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-nested-helper-arguments",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="nested-helper-arguments",
                required_outputs=["solver_results.json"],
            ),
            program=GeneratedProgram(
                source_code=source_code,
                declared_outputs=["solver_results.json"],
            ),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "passed"
    assert interpreter.execute_calls == 1


def test_program_executor_runs_real_local_solver_and_figure(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor

    source_code = """
from pathlib import Path
import json
import matplotlib.pyplot as plt

Path('solver_results.json').write_text(
    json.dumps({'verified_results': [{'id': 'fit_slope', 'value': 2.5}]}),
    encoding='utf-8',
)
Path('parameter_table.csv').write_text('symbol,value\\nslope,2.5\\n', encoding='utf-8')
Path('figures').mkdir(exist_ok=True)
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 2.5])
fig.savefig('figures/fit.png')
plt.close(fig)
print('solver complete')
""".strip()

    async def run():
        return await LocalProgramExecutor().execute(
            task_id="real-runtime",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(
                review_status="approved",
                model_id="smoke-fit",
                required_outputs=["solver_results.json", "parameter_table.csv", "figures/fit.png"],
            ),
            program=GeneratedProgram(
                source_code=source_code,
                declared_outputs=["solver_results.json", "parameter_table.csv", "figures/fit.png"],
            ),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "passed"
    assert manifest.execution_count == 1
    assert "solver complete" in manifest.stdout
    assert (tmp_path / "figures" / "fit.png").stat().st_size > 0
