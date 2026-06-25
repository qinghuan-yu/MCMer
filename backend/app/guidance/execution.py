"""Execute one complete generated Python solver and record its evidence."""

from __future__ import annotations

import ast
import glob
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Protocol

from app.guidance.contracts import ApprovedModelSpec, ExecutionManifest, GeneratedProgram
from app.tools.local_interpreter import LocalCodeInterpreter


class UnsafeGeneratedProgramError(ValueError):
    pass


class UnapprovedModelError(ValueError):
    pass


class ProgramInterpreter(Protocol):
    async def execute_code(self, code: str) -> tuple[str, bool, str]: ...
    async def save_notebook(self, filepath: str) -> None: ...
    async def close(self) -> None: ...


class LocalProgramExecutor:
    """Run a reviewed solver exactly once through the local interpreter."""

    def __init__(
        self,
        *,
        interpreter_factory: Callable[[Path], ProgramInterpreter] | None = None,
    ) -> None:
        self.interpreter_factory = interpreter_factory or (
            lambda work_dir: LocalCodeInterpreter(work_dir=str(work_dir))
        )

    async def execute(
        self,
        *,
        task_id: str,
        work_dir: str | Path,
        approved_model: ApprovedModelSpec,
        program: GeneratedProgram,
    ) -> ExecutionManifest:
        if approved_model.review_status != "approved":
            raise UnapprovedModelError("solver execution requires an approved model")
        if program.entrypoint != "solve.py":
            raise ValueError("Python guidance programs must use solve.py as the entrypoint")

        required_contract = [_normalize_relative_path(path) for path in approved_model.required_outputs]
        declared_contract = [_normalize_relative_path(path) for path in program.declared_outputs]
        if required_contract != declared_contract:
            raise ValueError(
                f"declared output mismatch: required={required_contract}; declared={declared_contract}"
            )

        _validate_generated_program(program.source_code)
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)
        program_path = root / program.entrypoint
        program_path.write_text(program.source_code, encoding="utf-8")

        interpreter = self.interpreter_factory(root)
        started = time.monotonic()
        output = ""
        error_occurred = False
        error_message = ""
        try:
            try:
                output, error_occurred, error_message = await interpreter.execute_code(program.source_code)
                await interpreter.save_notebook(str(root / "notebook.ipynb"))
            except Exception as exc:
                error_occurred = True
                error_message = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                await interpreter.close()
            except Exception as exc:
                error_occurred = True
                close_error = f"{type(exc).__name__}: {exc}"
                error_message = "; ".join(item for item in (error_message, close_error) if item)

        generated, missing = _resolve_required_outputs(root, approved_model.required_outputs)
        if (root / "notebook.ipynb").is_file():
            generated.append("notebook.ipynb")

        status = "passed"
        if error_occurred:
            status = "failed"
        elif missing:
            status = "partial"

        manifest = ExecutionManifest(
            task_id=task_id,
            model_id=approved_model.model_id,
            status=status,
            entrypoint=program.entrypoint,
            program_sha256=hashlib.sha256(program.source_code.encode("utf-8")).hexdigest(),
            execution_count=1,
            elapsed_seconds=round(time.monotonic() - started, 6),
            stdout=output,
            error_message=error_message,
            generated_files=generated,
            missing_required_outputs=missing,
        )
        (root / "execution_manifest.json").write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest


def _validate_generated_program(source_code: str) -> None:
    try:
        tree = ast.parse(source_code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeGeneratedProgramError(f"generated solve.py is not valid Python: {exc}") from exc

    forbidden_modules = {"subprocess", "socket", "ctypes", "multiprocessing"}
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    forbidden_attributes = {("os", "system"), ("os", "popen")}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".", 1)[0] for alias in node.names}
            if names & forbidden_modules:
                raise UnsafeGeneratedProgramError(f"forbidden import: {sorted(names & forbidden_modules)[0]}")
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "").split(".", 1)[0]
            if module in forbidden_modules:
                raise UnsafeGeneratedProgramError(f"forbidden import: {module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
            raise UnsafeGeneratedProgramError(f"forbidden call: {node.func.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in forbidden_attributes:
                raise UnsafeGeneratedProgramError(f"forbidden call: {owner.id}.{node.func.attr}")


def _normalize_relative_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().lstrip("/")
    path = Path(normalized)
    if not normalized or ".." in path.parts or path.is_absolute():
        raise ValueError(f"required output must be a safe relative path: {value}")
    return path.as_posix()


def _resolve_required_outputs(root: Path, required_outputs: list[str]) -> tuple[list[str], list[str]]:
    generated: list[str] = []
    missing: list[str] = []
    for value in required_outputs:
        pattern = _normalize_relative_path(value)
        matches = sorted(path for path in root.glob(pattern) if path.is_file())
        if not matches:
            missing.append(pattern)
            continue
        if glob.has_magic(pattern):
            generated.extend(path.relative_to(root).as_posix() for path in matches)
        else:
            generated.append(pattern)
    return list(dict.fromkeys(generated)), list(dict.fromkeys(missing))
