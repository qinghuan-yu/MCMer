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


def test_program_executor_rejects_unsafe_generated_code_before_execution(tmp_path: Path) -> None:
    from app.guidance.contracts import ApprovedModelSpec, GeneratedProgram
    from app.guidance.execution import LocalProgramExecutor, UnsafeGeneratedProgramError

    interpreter = FakeInterpreter(tmp_path)
    program = GeneratedProgram(
        language="python",
        entrypoint="solve.py",
        source_code="import subprocess\nsubprocess.run(['cmd', '/c', 'echo unsafe'])",
    )

    async def run():
        executor = LocalProgramExecutor(interpreter_factory=lambda work_dir: interpreter)
        return await executor.execute(
            task_id="task-unsafe",
            work_dir=tmp_path,
            approved_model=ApprovedModelSpec(review_status="approved", model_id="unsafe"),
            program=program,
        )

    with pytest.raises(UnsafeGeneratedProgramError):
        asyncio.run(run())

    assert interpreter.execute_calls == 0


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
            program=GeneratedProgram(source_code=source_code),
        )

    manifest = asyncio.run(run())

    assert manifest.status == "passed"
    assert manifest.execution_count == 1
    assert "solver complete" in manifest.stdout
    assert (tmp_path / "figures" / "fit.png").stat().st_size > 0
