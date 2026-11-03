"""figure feasibility responsibilities."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any
from app.artifacts.protocols import DEFAULT_FIGURE_CAPABILITIES, FigureRequestState, RESULT_OVERVIEW_ROLE, WorkflowIssue, normalize_figure_role
from app.artifacts.figure_need import _norm_text


PLACEHOLDER_RESULT_IDS = {
    "verified_series",
    "result_series",
    "placeholder_result",
    "todo_result",
}


def _discover_input_files(work_dir: str) -> list[str]:
    root = Path(work_dir)
    discovered: list[str] = []
    for folder in (root / "inputs", root / "data", root):
        if not folder.exists() or not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json"}:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except Exception:
                rel = path.name
            discovered.append(rel)
    return list(dict.fromkeys(discovered))


def _load_verified_result_ids(work_dir: str) -> set[str]:
    path = Path(work_dir) / "result_registry.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, dict):
        return set()
    verified = payload.get("verified_results", [])
    if not isinstance(verified, list):
        return set()
    return {
        str(item.get("id") or "").strip()
        for item in verified
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


def _load_result_registry(work_dir: str) -> dict[str, Any]:
    path = Path(work_dir) / "result_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_capability_constraints(requests: list[dict[str, Any]],
    chart_language: str,
    available_data_files: list[str],
    verified_result_ids: set[str],
    work_dir: str,
) -> list[dict[str, Any]]:
    available_set = {str(item).strip().replace("\\", "/") for item in available_data_files if str(item).strip()}
    root = Path(work_dir)
    evaluated: list[dict[str, Any]] = []

    for raw in requests:
        if not isinstance(raw, dict):
            continue
        req = dict(raw)
        raw_role = _norm_text(req.get("semantic_role") or req.get("role")).lower()
        role = "data_overview" if raw_role == "data_overview" else normalize_figure_role(raw_role)
        kind = _norm_text(req.get("figure_kind") or "result_figure").lower()
        required = bool(req.get("required", True))
        req.setdefault("figure_kind", kind or "result_figure")
        req["semantic_role"] = role
        req.setdefault("role", role)

        depends_on = req.get("depends_on", {}) if isinstance(req.get("depends_on"), dict) else {}
        data_files = depends_on.get("data_files", []) if isinstance(depends_on.get("data_files"), list) else []
        required_data = req.get("required_data", []) if isinstance(req.get("required_data"), list) else []
        data_files_list = [
            str(item).strip().replace("\\", "/")
            for item in [*data_files, *required_data]
            if str(item).strip()
        ]
        data_files_list = list(dict.fromkeys(data_files_list))

        linked_ids = req.get("linked_result_ids", [])
        if not isinstance(linked_ids, list):
            linked_ids = []
        if not linked_ids:
            linked_ids = depends_on.get("result_ids", []) if isinstance(depends_on.get("result_ids"), list) else []
        linked_ids_list = [str(item).strip() for item in linked_ids if str(item).strip()]
        placeholder_ids = {item for item in linked_ids_list if item.lower() in PLACEHOLDER_RESULT_IDS}
        linked_ids_list = [item for item in linked_ids_list if item.lower() not in PLACEHOLDER_RESULT_IDS]

        capability = DEFAULT_FIGURE_CAPABILITIES.get(role)
        if (
            kind == "result_figure"
            and bool(capability and capability.requires_verified_result)
            and not linked_ids_list
            and verified_result_ids
            and not placeholder_ids
        ):
            linked_ids_list = sorted(str(item).strip() for item in verified_result_ids if str(item).strip())[:12]
            req["auto_bound_verified_results"] = True

        req["linked_result_ids"] = linked_ids_list
        depends_on["result_ids"] = linked_ids_list
        depends_on["data_files"] = data_files_list
        req["depends_on"] = depends_on
        req["required_data"] = data_files_list

        blocked_reason = ""

        def _collect_header_tokens(files: list[str]) -> set[str]:
            tokens: set[str] = set()
            for rel in files:
                path = Path(rel)
                name = path.name.lower()
                for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", name):
                    token = token.strip().lower()
                    if token:
                        tokens.add(token)
                disk_path = root / rel
                if not disk_path.exists() or not disk_path.is_file():
                    continue
                try:
                    if disk_path.suffix.lower() == ".csv":
                        with disk_path.open("r", encoding="utf-8", newline="") as fp:
                            reader = csv.reader(fp)
                            header = next(reader, [])
                    elif disk_path.suffix.lower() in {".xlsx", ".xls"}:
                        from openpyxl import load_workbook  # type: ignore

                        wb = load_workbook(disk_path, read_only=True, data_only=True)
                        ws = wb.active
                        first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), [])
                        header = list(first_row or [])
                    else:
                        header = []
                except Exception:
                    header = []
                for column in header:
                    for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", str(column or "").lower()):
                        token = token.strip().lower()
                        if token:
                            tokens.add(token)
            return tokens

        def _count_numeric_columns(files: list[str]) -> int:
            numeric_columns: set[str] = set()
            for rel in files:
                disk_path = root / rel
                if not disk_path.exists() or not disk_path.is_file():
                    continue
                suffix = disk_path.suffix.lower()
                try:
                    headers: list[str] = []
                    rows: list[list[object]] = []
                    if suffix == ".csv":
                        with disk_path.open("r", encoding="utf-8", newline="") as fp:
                            reader = csv.reader(fp)
                            headers = [str(item or "").strip() for item in next(reader, [])]
                            for _, row in zip(range(120), reader):
                                rows.append(list(row))
                    elif suffix in {".xlsx", ".xls"}:
                        from openpyxl import load_workbook  # type: ignore

                        wb = load_workbook(disk_path, read_only=True, data_only=True)
                        ws = wb.active
                        it = ws.iter_rows(min_row=1, max_row=140, values_only=True)
                        header_row = next(it, [])
                        headers = [str(item or "").strip() for item in (header_row or [])]
                        for row in it:
                            rows.append(list(row or []))
                    else:
                        continue

                    for idx, name in enumerate(headers):
                        if not name:
                            continue
                        numeric_hits = 0
                        for row in rows:
                            val = row[idx] if idx < len(row) else ""
                            try:
                                text = str(val or "").strip()
                                if not text:
                                    continue
                                float(text)
                                numeric_hits += 1
                            except Exception:
                                continue
                        if numeric_hits >= 2:
                            numeric_columns.add(name.lower())
                except Exception:
                    continue
            return len(numeric_columns)

        request_context = "\n".join(
            [
                _norm_text(req.get("purpose")),
                _norm_text(req.get("chart_intent")),
                " ".join(str(item) for item in req.get("required_columns", []) if str(item).strip()) if isinstance(req.get("required_columns"), list) else "",
                " ".join(str(item) for item in req.get("expected_content", []) if str(item).strip()) if isinstance(req.get("expected_content"), list) else "",
            ]
        ).lower()
        header_tokens = _collect_header_tokens(data_files_list)

        if not capability:
            blocked_reason = f"unsupported_renderer:{role or 'unknown'}"
        elif chart_language not in set(capability.supports_languages):
            blocked_reason = f"unsupported_chart_language:{chart_language}"
        elif capability.domain_applicability and kind == "result_figure":
            applies = any(token.lower() in request_context for token in capability.domain_applicability)
            if not applies:
                applies = any(token.lower() in header_tokens for token in capability.domain_applicability)
            if not applies:
                blocked_reason = f"semantic_data_mismatch:{role}"
        if not blocked_reason and bool(capability and capability.requires_data) and kind == "result_figure":
            candidates = [item for item in data_files_list if item != "result_registry.json"]
            if not candidates:
                blocked_reason = "missing_source_data"
            elif not any(item in available_set for item in candidates):
                blocked_reason = "missing_source_data"
            elif role in {"fitting_curve", "residual_plot", "comparison_bar"}:
                required_columns = [
                    str(item).strip().lower()
                    for item in (req.get("required_columns", []) if isinstance(req.get("required_columns"), list) else [])
                    if str(item).strip()
                ]
                if required_columns and not any(col in header_tokens for col in required_columns):
                    blocked_reason = f"required_columns_missing:{role}"
            elif role in {RESULT_OVERVIEW_ROLE, "data_overview"}:
                if _count_numeric_columns(candidates) < 2:
                    blocked_reason = f"insufficient_numeric_columns:{role}"
        if not blocked_reason and bool(capability and capability.requires_verified_result) and kind == "result_figure":
            if placeholder_ids:
                blocked_reason = "placeholder_linked_result_ids"
            elif not linked_ids_list:
                blocked_reason = "missing_verified_result_binding"
            elif not verified_result_ids:
                blocked_reason = "pending_result_binding"
            elif verified_result_ids and any(item not in verified_result_ids for item in linked_ids_list):
                blocked_reason = "linked_result_not_verified"

        if kind == "result_figure" and bool(capability and capability.requires_verified_result):
            if blocked_reason == "pending_result_binding":
                req["result_binding_status"] = "pending_result_binding"
            elif not blocked_reason:
                req["result_binding_status"] = "verified_result_bound"
            else:
                req["result_binding_status"] = "binding_blocked"
        else:
            req["result_binding_status"] = "not_required"

        if blocked_reason:
            req["required"] = False
            req["must_include_in_paper"] = False
            req["recommended"] = True
            req["status"] = FigureRequestState.blocked.value
            req["blocked_reason"] = blocked_reason
            req["feasible"] = False
        else:
            req["feasible"] = True
            if required:
                req["required"] = True
                req["must_include_in_paper"] = bool(req.get("must_include_in_paper", True))
                req["recommended"] = False
                req["status"] = FigureRequestState.required.value
                req.pop("blocked_reason", None)
            else:
                req["required"] = False
                req["must_include_in_paper"] = False
                req["recommended"] = True
                req["status"] = FigureRequestState.recommended.value
                req.pop("blocked_reason", None)
        evaluated.append(req)

    return evaluated


def _issues_from_requests(requests: list[dict[str, Any]]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip() != FigureRequestState.blocked.value:
            continue
        request_id = str(item.get("id") or "").strip()
        reason = str(item.get("blocked_reason") or "").strip() or "blocked"
        issues.append(
            WorkflowIssue(
                code="FIGURE_REQUEST_BLOCKED",
                severity="warning",
                stage="figure_plan",
                reason=reason,
                request_id=request_id,
                action="Implement renderer, provide source data, or keep downgraded as recommended.",
            )
        )
    return issues
