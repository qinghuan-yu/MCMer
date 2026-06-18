"""Run solve specs through deterministic local calculations.

The runner is intentionally small: it turns a structured solve spec into
registries on disk so long calculations do not depend on LLM tool-call turns.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


def run_solve_spec(solve_spec: dict[str, Any], work_dir: str | Path) -> dict[str, Any]:
    """Execute supported deterministic solve-spec operations and write artifacts."""
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)

    spec_id = str(solve_spec.get("spec_id") or "solve_spec")
    subproblem_id = str(solve_spec.get("subproblem_id") or "")
    values: dict[str, float | int] = {}
    parameters_by_index: dict[int, dict[str, Any]] = {}
    blocked_results: list[dict[str, Any]] = []
    pending_formulas: list[tuple[int, dict[str, Any]]] = []

    raw_parameters = solve_spec.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raw_parameters = []

    for index, raw_parameter in enumerate(raw_parameters):
        if not isinstance(raw_parameter, dict):
            continue
        if "formula" in raw_parameter:
            # Validate syntax before dependency deferral so unsafe expressions
            # can never be disguised by an unresolved parameter.
            _validate_formula(str(raw_parameter.get("formula") or ""))
            pending_formulas.append((index, raw_parameter))
            continue
        parameter = _compute_parameter(raw_parameter, values)
        _register_parameter(parameter, values, blocked_results, subproblem_id)
        parameters_by_index[index] = parameter

    while pending_formulas:
        deferred: list[tuple[int, dict[str, Any]]] = []
        progress = False
        for index, raw_parameter in pending_formulas:
            try:
                parameter = _compute_parameter(raw_parameter, values)
            except UnknownParameterError:
                deferred.append((index, raw_parameter))
                continue
            _register_parameter(parameter, values, blocked_results, subproblem_id)
            parameters_by_index[index] = parameter
            progress = True
        pending_formulas = deferred
        if not progress:
            break

    for index, raw_parameter in pending_formulas:
        formula = str(raw_parameter.get("formula") or "")
        missing = sorted(symbol for symbol in _formula_dependencies(formula) if symbol not in values)
        parameter = _blocked_parameter(
            raw_parameter,
            reason=f"formula dependencies are unresolved: {', '.join(missing) or 'unknown'}",
            next_required_data=missing,
        )
        _register_parameter(parameter, values, blocked_results, subproblem_id)
        parameters_by_index[index] = parameter

    parameters = [parameters_by_index[index] for index in sorted(parameters_by_index)]

    verified_results = _build_verified_results(
        solve_spec.get("results", []),
        parameters,
        subproblem_id=subproblem_id,
    )
    _link_parameters_to_results(parameters, verified_results)

    result_registry = _result_registry(verified_results, blocked_results)
    parameter_registry = _parameter_registry(parameters)
    status = "passed" if not blocked_results else "partial"
    report = {
        "spec_id": spec_id,
        "status": status,
        "artifacts": {
            "result_registry": "result_registry.json",
            "parameter_registry": "parameter_registry.json",
            "compute_run_manifest": "compute_run_manifest.json",
        },
    }

    _write_json(root / "result_registry.json", result_registry)
    _write_json(root / "parameter_registry.json", parameter_registry)
    _write_json(root / "compute_run_manifest.json", report)
    return report


def _compute_parameter(raw: dict[str, Any], values: dict[str, float | int]) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or raw.get("id") or raw.get("name") or "").strip()
    identifiability = str(raw.get("identifiability") or "").strip().lower()
    if identifiability in {"non_identifiable", "unidentifiable"}:
        return {
            **_base_parameter(raw, symbol),
            "value": raw.get("value"),
            "value_type": "unknown",
            "source_type": "blocked_non_identifiable",
            "trust_status": "blocked",
            "reason": str(raw.get("reason") or "parameter is not identifiable from available data"),
            "next_required_data": _text_list(raw.get("next_required_data")),
        }

    if "formula" in raw:
        value = _safe_eval_arithmetic(str(raw.get("formula") or ""), values)
        source_type = str(raw.get("source_type") or "computed_from_registered_parameters")
        return {
            **_base_parameter(raw, symbol),
            "value": value,
            "value_type": "estimated",
            "source_type": source_type,
            "trust_status": str(raw.get("trust_status") or "estimated"),
            "estimation_method": str(raw.get("estimation_method") or raw.get("method") or "deterministic_formula"),
        }

    if raw.get("value") is None or raw.get("value") == "":
        return _blocked_parameter(
            raw,
            reason=str(raw.get("reason") or "parameter has no registered value or computable formula"),
            next_required_data=_text_list(raw.get("next_required_data")) or [symbol],
        )

    source_type = str(raw.get("source_type") or "problem_or_input")
    return {
        **_base_parameter(raw, symbol),
        "value": raw.get("value"),
        "value_type": str(raw.get("value_type") or _value_type(source_type)),
        "source_type": source_type,
        "trust_status": str(raw.get("trust_status") or _trust_status(source_type)),
        "estimation_method": str(raw.get("estimation_method") or raw.get("method") or ""),
    }


def _blocked_parameter(
    raw: dict[str, Any],
    *,
    reason: str,
    next_required_data: list[str],
) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or raw.get("id") or raw.get("name") or "").strip()
    return {
        **_base_parameter(raw, symbol),
        "value": raw.get("value"),
        "value_type": "unknown",
        "source_type": "blocked_unresolved_dependency",
        "trust_status": "blocked",
        "reason": reason,
        "next_required_data": next_required_data,
    }


def _register_parameter(
    parameter: dict[str, Any],
    values: dict[str, float | int],
    blocked_results: list[dict[str, Any]],
    subproblem_id: str,
) -> None:
    symbol = str(parameter.get("symbol") or "")
    if symbol and parameter.get("trust_status") != "blocked":
        value = parameter.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[symbol] = value
    if parameter.get("trust_status") == "blocked":
        blocked_results.append(_blocked_result(parameter, subproblem_id))


def _base_parameter(raw: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "id": f"param_{_slug(symbol)}",
        "symbol": symbol,
        "name": str(raw.get("name") or symbol),
        "meaning": str(raw.get("meaning") or raw.get("description") or ""),
        "unit": str(raw.get("unit") or ""),
        "source_ref": str(raw.get("source_ref") or ""),
        "source_location": str(raw.get("source_location") or ""),
        "linked_result_ids": [],
        "confidence": str(raw.get("confidence") or ""),
        "notes": str(raw.get("notes") or ""),
    }


def _build_verified_results(
    raw_results: Any,
    parameters: list[dict[str, Any]],
    *,
    subproblem_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_results, list):
        return []
    by_symbol = {str(item.get("symbol")): item for item in parameters}
    verified: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        symbols = _text_list(raw.get("parameters"))
        if any(by_symbol.get(symbol, {}).get("trust_status") == "blocked" for symbol in symbols):
            continue
        result_id = str(raw.get("id") or f"result_{len(verified) + 1}")
        verified.append(
            {
                "id": result_id,
                "section": str(raw.get("section") or subproblem_id),
                "verified": True,
                "status": "verified",
                "result_type": str(raw.get("result_type") or "deterministic_calculation"),
                "claim_text": str(raw.get("claim_text") or ""),
                "parameters": [
                    by_symbol[symbol]
                    for symbol in symbols
                    if symbol in by_symbol and by_symbol[symbol].get("trust_status") != "blocked"
                ],
                "source_data": _text_list(raw.get("source_data")),
                "confidence_level": str(raw.get("confidence_level") or "medium"),
            }
        )
    return verified


def _link_parameters_to_results(parameters: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    by_symbol = {str(parameter.get("symbol")): parameter for parameter in parameters}
    for result in results:
        result_id = str(result.get("id") or "")
        for parameter in result.get("parameters", []):
            symbol = str(parameter.get("symbol") or "")
            if symbol in by_symbol and result_id:
                by_symbol[symbol]["linked_result_ids"] = [result_id]
                parameter["linked_result_ids"] = [result_id]


def _blocked_result(parameter: dict[str, Any], subproblem_id: str) -> dict[str, Any]:
    symbol = str(parameter.get("symbol") or "unknown")
    reason = (
        "non_identifiable_parameter"
        if parameter.get("source_type") == "blocked_non_identifiable"
        else "unresolved_parameter_dependency"
    )
    return {
        "id": f"blocked_{_slug(symbol)}",
        "section": subproblem_id,
        "status": "blocked",
        "reason": reason,
        "parameter_symbol": symbol,
        "message": str(parameter.get("reason") or ""),
        "next_required_data": _text_list(parameter.get("next_required_data")),
    }


def _result_registry(verified_results: list[dict[str, Any]], blocked_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "verified_results": verified_results,
        "blocked_results": blocked_results,
        "summary": {
            "verified_count": len(verified_results),
            "blocked_count": len(blocked_results),
            "coverage_status": "complete" if not blocked_results else "partial",
        },
    }


def _parameter_registry(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for parameter in parameters:
        status = str(parameter.get("trust_status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "parameters": parameters,
        "summary": {
            "total_count": len(parameters),
            "verified_count": by_status.get("verified", 0),
            "estimated_count": by_status.get("estimated", 0),
            "assumed_count": by_status.get("assumed", 0),
            "source_locked_count": by_status.get("source_locked", 0),
            "blocked_count": by_status.get("blocked", 0),
        },
    }


def _safe_eval_arithmetic(expression: str, values: dict[str, float | int]) -> float | int:
    tree = ast.parse(expression, mode="eval")
    _validate_formula_tree(tree)
    return _eval_node(tree.body, values)


class UnknownParameterError(ValueError):
    def __init__(self, symbol: str) -> None:
        super().__init__(symbol)
        self.symbol = symbol


def _validate_formula(expression: str) -> None:
    tree = ast.parse(expression, mode="eval")
    _validate_formula_tree(tree)


def _validate_formula_tree(tree: ast.AST) -> None:
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.UAdd,
        ast.USub,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError(f"Unsupported or unsafe formula expression: {ast.dump(node)}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError(f"Unsupported or unsafe formula expression: {ast.dump(node)}")


def _formula_dependencies(expression: str) -> set[str]:
    tree = ast.parse(expression, mode="eval")
    _validate_formula_tree(tree)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _eval_node(node: ast.AST, values: dict[str, float | int]) -> float | int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in values:
            return values[node.id]
        raise UnknownParameterError(node.id)
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left**right
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, values)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
    raise ValueError(f"Unsupported or unsafe formula expression: {ast.dump(node)}")


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _value_type(source_type: str) -> str:
    if source_type in {"problem_or_input", "problem_statement", "uploaded_data"}:
        return "source_locked"
    if "assum" in source_type:
        return "assumed"
    return "estimated"


def _trust_status(source_type: str) -> str:
    if source_type in {"problem_or_input", "problem_statement", "uploaded_data"}:
        return "source_locked"
    if "assum" in source_type:
        return "assumed"
    if "blocked" in source_type:
        return "blocked"
    return "estimated"


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "unknown"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
