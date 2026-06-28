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


def validate_generated_program_source(
    source_code: str,
    declared_outputs: list[str] | None = None,
) -> None:
    _validate_generated_program(source_code)
    if declared_outputs is not None:
        _validate_declared_output_locations(source_code, declared_outputs)


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

        validate_generated_program_source(program.source_code, program.declared_outputs)
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)
        _ensure_required_output_dirs(root, required_contract)
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
    untrusted_markers = {
        "示例": "示例",
        "占位": "占位",
        "模拟数据": "模拟数据",
        "随机数据": "随机数据",
        "兜底": "兜底",
        "placeholder": "placeholder",
        "dummy data": "dummy data",
    }
    lowered_source = source_code.lower()
    for marker, label in untrusted_markers.items():
        if marker.lower() in lowered_source:
            raise UnsafeGeneratedProgramError(f"untrustworthy generated solver uses {label}")

    try:
        tree = ast.parse(source_code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeGeneratedProgramError(f"generated solve.py is not valid Python: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _is_constraint_values_key(key) and _is_empty_list(value):
                    raise UnsafeGeneratedProgramError(
                        "constraint_values must be a non-empty numeric list in solver evidence"
                    )
        if isinstance(node, ast.Assign) and _is_empty_list(node.value):
            if any(_target_mentions_constraint_values(target) for target in node.targets):
                raise UnsafeGeneratedProgramError(
                    "constraint_values must be a non-empty numeric list in solver evidence"
                )
        if isinstance(node, ast.AnnAssign) and _is_empty_list(node.value):
            if _target_mentions_constraint_values(node.target):
                raise UnsafeGeneratedProgramError(
                    "constraint_values must be a non-empty numeric list in solver evidence"
                )

    forbidden_modules = {"subprocess", "socket", "ctypes", "multiprocessing"}
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    forbidden_attributes = {("os", "system"), ("os", "popen")}
    numpy_aliases = {"numpy"}
    random_aliases = {"random"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".", 1)[0] for alias in node.names}
            if names & forbidden_modules:
                raise UnsafeGeneratedProgramError(f"forbidden import: {sorted(names & forbidden_modules)[0]}")
            for alias in node.names:
                root_name = alias.name.split(".", 1)[0]
                local_name = alias.asname or root_name
                if root_name == "numpy":
                    numpy_aliases.add(local_name)
                if root_name == "random":
                    random_aliases.add(local_name)
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "").split(".", 1)[0]
            if module in forbidden_modules:
                raise UnsafeGeneratedProgramError(f"forbidden import: {module}")
            if module == "numpy":
                for alias in node.names:
                    if alias.name == "random":
                        raise UnsafeGeneratedProgramError("untrustworthy generated solver uses np.random")
            if module == "random":
                raise UnsafeGeneratedProgramError("untrustworthy generated solver uses random")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
            raise UnsafeGeneratedProgramError(f"forbidden call: {node.func.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in random_aliases:
                raise UnsafeGeneratedProgramError("untrustworthy generated solver uses random")
            if node.func.id == "least_squares" and any(keyword.arg == "constraints" for keyword in node.keywords):
                raise UnsafeGeneratedProgramError(
                    "least_squares does not support constraints; use scipy.optimize.minimize"
                )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in forbidden_attributes:
                raise UnsafeGeneratedProgramError(f"forbidden call: {owner.id}.{node.func.attr}")
            if _is_numpy_random_attribute(node.func, numpy_aliases):
                raise UnsafeGeneratedProgramError("untrustworthy generated solver uses np.random")
            if node.func.attr == "least_squares" and any(keyword.arg == "constraints" for keyword in node.keywords):
                raise UnsafeGeneratedProgramError(
                    "least_squares does not support constraints; use scipy.optimize.minimize"
                )


def _is_empty_list(node: ast.AST | None) -> bool:
    return isinstance(node, ast.List) and not node.elts


def _is_constraint_values_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "constraint_values"


def _target_mentions_constraint_values(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return "constraint_values" in node.id
    if isinstance(node, ast.Attribute):
        return "constraint_values" in node.attr or _target_mentions_constraint_values(node.value)
    if isinstance(node, ast.Subscript):
        return _is_constraint_values_key(node.slice) or _target_mentions_constraint_values(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_target_mentions_constraint_values(item) for item in node.elts)
    return False


def _is_numpy_random_attribute(node: ast.Attribute, numpy_aliases: set[str]) -> bool:
    value = node.value
    if not isinstance(value, ast.Attribute) or value.attr != "random":
        return False
    owner = value.value
    return isinstance(owner, ast.Name) and owner.id in numpy_aliases


def _validate_declared_output_locations(source_code: str, declared_outputs: list[str]) -> None:
    literals = {_normalize_literal_path(value) for value in _string_literals(source_code)}
    for declared in declared_outputs:
        normalized = _normalize_relative_path(declared)
        if glob.has_magic(normalized):
            continue
        misplaced = sorted(
            literal
            for literal in literals
            if literal.endswith(f"/{normalized}") and literal != normalized
        )
        if misplaced and normalized not in literals:
            raise UnsafeGeneratedProgramError(
                f"declared output {normalized} must be written exactly at that relative path, not {misplaced[0]}"
            )


def _string_literals(source_code: str) -> list[str]:
    try:
        tree = ast.parse(source_code, mode="exec")
    except SyntaxError:
        return []
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _normalize_literal_path(value: str) -> str:
    return value.replace("\\", "/").strip().lstrip("./")


def _normalize_relative_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().lstrip("/")
    path = Path(normalized)
    if not normalized or ".." in path.parts or path.is_absolute():
        raise ValueError(f"required output must be a safe relative path: {value}")
    return path.as_posix()


def _ensure_required_output_dirs(root: Path, required_outputs: list[str]) -> None:
    for value in required_outputs:
        if glob.has_magic(value):
            parent = Path(value).parent
        else:
            parent = Path(value).parent
        if str(parent) not in {"", "."}:
            (root / parent).mkdir(parents=True, exist_ok=True)


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
