"""coder artifacts responsibilities."""

from __future__ import annotations

from app.utils.log_util import logger
from app.utils.figure_artifacts import PAPER_FIGURE_DIRS, is_image_path, is_verified_paper_figure, normalize_artifact_path
from pathlib import Path
import csv
import importlib.metadata
import json
import re


def _sanitize_section_name(section: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", (section or "result").strip())
    sanitized = sanitized.strip("_")
    return sanitized or "result"


def _dedupe_jsonish_items(items: list[object]) -> list[object]:
    deduped: list[object] = []
    seen: set[str] = set()
    for item in items:
        if item is None or item == "":
            continue
        try:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            marker = str(item)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def _is_number_like(value: object) -> bool:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _salvage_existing_payload_key_results(payload: object,
    source_name: str,
    subtask_title: str,
    limit: int = 30,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []

    def walk(node: object, path_parts: list[str]) -> None:
        if len(results) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, [*path_parts, str(key)])
            return
        if isinstance(node, list):
            for index, value in enumerate(node, start=1):
                walk(value, [*path_parts, str(index)])
            return
        if not _is_number_like(node):
            return

        readable_path = " / ".join(part for part in path_parts if part)
        if not readable_path:
            readable_path = subtask_title
        path_marker = readable_path.lower()
        metadata_markers = [
            "metadata",
            "result_registry_summary",
            "blocked_count",
            "verified_count",
            "warning_count",
            "info_count",
            "expected_but_missing",
            "generated_at",
        ]
        if any(marker in path_marker for marker in metadata_markers):
            return
        result_markers = [
            "关键结果",
            "结果",
            "统计",
            "拟合",
            "误差",
            "参数",
            "指标",
            "预测",
            "校正",
            "阶段",
            "异常",
            "阈值",
            "rmse",
            "mae",
            "r2",
            "r²",
            "metric",
            "score",
        ]
        if not any(marker in path_marker for marker in result_markers):
            return
        value_text = str(node).strip()
        result_id = _sanitize_section_name(f"{source_name}_{readable_path}")
        results.append(
            {
                "id": result_id,
                "name": readable_path,
                "value": value_text,
                "unit": "",
                "formula": "",
                "inputs": {},
                "source_data": [source_name],
                "code_cell": "",
                "evidence": f"从阻断前已写入的结构化结果 {source_name} 中自动保全数值：{readable_path} = {value_text}",
                "source": "existing_structured_payload_salvage",
                "verified": False,
                "status": "unverified",
                "warnings": ["salvaged_from_interrupted_payload_requires_formula_and_unit_verification"],
            }
        )

    walk(payload, [])
    return results


def _csv_summary_result(csv_path: Path,
    rows: list[dict[str, str]],
    subtask_title: str,
    max_preview_rows: int = 8,
) -> dict[str, object] | None:
    if not rows:
        return None

    columns = list(rows[0].keys())
    preview_lines = [" | ".join(columns)]
    preview_lines.append(" | ".join("---" for _ in columns))
    for row in rows[:max_preview_rows]:
        preview_lines.append(" | ".join(str(row.get(column, "")).strip() for column in columns))

    result_id = _sanitize_section_name(f"{csv_path.stem}_result_table")
    return {
        "id": result_id,
        "name": f"{csv_path.stem} 结果表",
        "value": "\n".join(preview_lines),
        "unit": "",
        "formula": "",
        "inputs": {"row_count": len(rows), "columns": columns},
        "source_data": [csv_path.name],
        "code_cell": "",
        "evidence": (
            f"从已生成结果文件 {csv_path.name} 自动保全表格摘要，共 {len(rows)} 行；"
            f"预览前 {min(len(rows), max_preview_rows)} 行，供论文写作引用完整结果来源。"
        ),
        "source": "artifact_salvage_summary",
        "verified": False,
        "status": "unverified",
        "warnings": ["artifact_table_summary_requires_formula_and_unit_verification"],
    }


def _salvage_artifact_key_results(work_dir: str, subtask_title: str, limit: int = 40) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    root = Path(work_dir)
    if not root.exists():
        return results

    for csv_path in sorted(root.glob("*.csv")):
        if len(results) >= limit:
            break
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                rows = [dict(row) for row in reader]
            summary = _csv_summary_result(csv_path, rows, subtask_title)
            if summary:
                results.append(summary)

            if len(results) >= limit:
                break

            columns = list(rows[0].keys()) if rows else []
            parameter_column = next((column for column in columns if "参数" in column or column.lower() in {"param", "parameter"}), "")
            value_columns = [
                column
                for column in columns
                if column != parameter_column and ("值" in column or "value" in column.lower() or "参数" in column)
            ]
            is_parameter_table = bool(parameter_column and value_columns)
            if not is_parameter_table:
                continue

            for row_index, row in enumerate(rows, start=1):
                if len(results) >= limit:
                    break
                parameter_name = str(row.get(parameter_column, "") or f"row{row_index}").strip()
                for column in value_columns:
                    if len(results) >= limit:
                        break
                    value_text = str(row.get(column) or "").strip()
                    if not _is_number_like(value_text):
                        continue
                    result_id = _sanitize_section_name(f"{csv_path.stem}_{parameter_name}_{column}")
                    display_name = f"{parameter_name}（{column}）" if parameter_name else str(column or result_id)
                    results.append(
                        {
                            "id": result_id,
                            "name": display_name,
                            "value": value_text,
                            "unit": "",
                            "formula": "",
                            "inputs": {},
                            "source_data": [csv_path.name],
                            "code_cell": "",
                            "evidence": f"从已生成参数文件 {csv_path.name} 第 {row_index} 行自动保全，避免工具预算阻断后丢失已计算参数。",
                            "source": "artifact_salvage",
                            "verified": False,
                            "status": "unverified",
                            "warnings": ["artifact_scalar_salvage_requires_formula_and_unit_verification"],
                        }
                    )
        except Exception as exc:
            logger.warning("自动保全 CSV 结果失败 {}: {}", csv_path, exc)
    return results


def _list_existing_artifacts(work_dir: str) -> list[str]:
    root = Path(work_dir)
    if not root.exists():
        return []
    suffixes = {".csv", ".json", ".txt", ".md", ".png", ".jpg", ".jpeg", ".svg"}
    ignored = {"task_info.json", "problem_facts.json", "solve_spec.json", "verify_plan.json", "result_registry.json"}
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in suffixes and path.name not in ignored
    )


def _list_existing_image_evidence(work_dir: str) -> list[str]:
    root = Path(work_dir)
    if not root.exists():
        return []
    suffixes = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
    evidence: list[str] = []
    for dirname in ("debug_artifacts", "output", "figures", "final_results"):
        folder = root / dirname
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() in suffixes:
                evidence.append(path.relative_to(root).as_posix())
    return evidence


def _looks_like_non_mutating_summary_code(code: str) -> bool:
    normalized = (code or "").lower()
    if not normalized.strip():
        return False

    summary_markers = [
        "最终总结报告",
        "检查是否还有其他需要修复的问题",
        "所有检查通过",
        "任务已完成",
        "print(",
    ]
    has_summary_intent = any(marker.lower() in normalized for marker in summary_markers)
    if not has_summary_intent:
        return False

    write_markers = [
        "write_text(",
        "write_bytes(",
        "to_csv(",
        "to_excel(",
        "to_json(",
        "json.dump(",
        "savefig(",
        "document.save(",
        "open(",
    ]

    if "open(" in normalized:
        if any(mode in normalized for mode in ["'w'", '"w"', "'a'", '"a"', "'wb'", '"wb"']):
            return False

    return not any(marker in normalized for marker in write_markers if marker != "open(")


def _installed_package_summary(limit: int = 80) -> str:
    preferred = [
        "numpy", "pandas", "scipy", "matplotlib", "seaborn", "openpyxl",
        "pypdf", "python-docx", "aiofiles", "fastapi", "pydantic",
    ]
    installed = {dist.metadata.get("Name", "") for dist in importlib.metadata.distributions()}
    ordered = [name for name in preferred if name in installed]
    remaining = sorted(name for name in installed if name and name not in ordered)
    package_names = ordered + remaining
    if len(package_names) > limit:
        package_names = package_names[:limit] + ["..."]
    return ", ".join(package_names)


def _validate_structured_result_file(file_path: Path, expected_section: str) -> tuple[bool, str]:
    if not file_path.exists() or not file_path.is_file():
        return False, "结构化结果文件不存在"

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"结构化结果文件不是有效 JSON: {exc}"

    if not isinstance(payload, dict):
        return False, "结构化结果文件顶层必须是 JSON 对象"

    required_keys = ["section", "summary", "key_results", "generated_files", "warnings"]
    missing = [key for key in required_keys if key not in payload]
    if missing:
        return False, f"结构化结果文件缺少字段: {', '.join(missing)}"

    if not isinstance(payload.get("summary"), str) or not payload.get("summary", "").strip():
        return False, "summary 必须是非空字符串"

    if not isinstance(payload.get("key_results"), list):
        return False, "key_results 必须是数组"

    if not isinstance(payload.get("generated_files"), list):
        return False, "generated_files 必须是数组"

    if not isinstance(payload.get("warnings"), list):
        return False, "warnings 必须是数组"

    final_image_prefixes = PAPER_FIGURE_DIRS
    for index, generated_file in enumerate(payload.get("generated_files", []), start=1):
        raw_path = ""
        if isinstance(generated_file, str):
            raw_path = generated_file
        elif isinstance(generated_file, dict):
            raw_path = (
                generated_file.get("path")
                or generated_file.get("filename")
                or generated_file.get("file")
                or generated_file.get("name")
                or ""
            )
        else:
            return False, f"generated_files[{index}] must be a string or object"

        normalized_path = normalize_artifact_path(raw_path)
        if not normalized_path:
            return False, f"generated_files[{index}] is missing a file path"
        if not is_image_path(normalized_path):
            continue

        if isinstance(generated_file, str):
            return False, (
                f"generated_files[{index}] image entries must be objects with "
                "path/kind/paper_ready/visible_text_language/chart_language_verified/"
                "visible_text_audit; bare image paths are not accepted"
            )

        paper_ready = bool(generated_file.get("paper_ready"))
        is_final_dir = normalized_path.startswith(final_image_prefixes)
        if paper_ready and not is_final_dir:
            return False, f"generated_files[{index}] paper_ready images must be under output/, figures/, or final_results/"
        if paper_ready and normalized_path.startswith("debug_artifacts/"):
            return False, f"generated_files[{index}] paper_ready images cannot be under debug_artifacts/"

        if is_final_dir or paper_ready:
            expected_language = (
                str(generated_file.get("expected_chart_language") or generated_file.get("document_language") or "").strip()
                or str(generated_file.get("visible_text_language") or "").strip()
            )
            if not expected_language:
                return False, f"generated_files[{index}] final image is missing visible_text_language"
            if not is_verified_paper_figure(generated_file, expected_language, file_path.parent):
                return False, f"generated_files[{index}] final image must satisfy the FigureArtifact contract"

    if payload.get("section") != expected_section:
        return False, f"section 字段必须为 {expected_section}"

    key_results = payload.get("key_results", [])
    if not key_results:
        return False, "key_results 不能为空"

    status_allowed = {"verified", "mismatch", "blocked", "unverified"}
    verification_section = expected_section == "数值复核"

    for index, item in enumerate(key_results, start=1):
        if not isinstance(item, dict):
            return False, f"key_results[{index}] 必须是对象"

        common_required = ["id", "name", "verified", "status", "warnings"]
        common_missing = [key for key in common_required if key not in item]
        if common_missing:
            return False, f"key_results[{index}] 缺少字段: {', '.join(common_missing)}"

        if str(item.get("status", "")).strip() not in status_allowed:
            return False, f"key_results[{index}].status 非法"

        if not isinstance(item.get("warnings"), list):
            return False, f"key_results[{index}].warnings 必须是数组"

        if verification_section:
            required = ["unit", "source_data"]
            missing_fields = [key for key in required if key not in item]
            if missing_fields:
                return False, f"key_results[{index}] 缺少字段: {', '.join(missing_fields)}"

            if not isinstance(item.get("source_data"), list):
                return False, f"key_results[{index}].source_data 必须是数组"

            has_verification_value = any(
                item.get(key) not in {None, "", "见论文", "无法确认"}
                for key in ["paper_value", "value", "computed_value"]
            )
            if not has_verification_value:
                return False, f"key_results[{index}] 缺少可复核的 paper_value/value/computed_value"

            if item.get("status") == "verified" and not item.get("source_data"):
                return False, f"key_results[{index}] 为 verified 时 source_data 不能为空"
        else:
            required = [
                "value",
                "unit",
                "formula",
                "inputs",
                "source_data",
                "code_cell",
                "evidence",
                "source",
            ]
            missing_fields = [key for key in required if key not in item]
            if missing_fields:
                return False, f"key_results[{index}] 缺少字段: {', '.join(missing_fields)}"

            if not isinstance(item.get("inputs"), dict):
                return False, f"key_results[{index}].inputs 必须是对象"

            if not isinstance(item.get("source_data"), list):
                return False, f"key_results[{index}].source_data 必须是数组"

            if item.get("status") == "verified":
                if item.get("value") in {None, "", "见论文", "无法确认"}:
                    return False, f"key_results[{index}] 为 verified 时 value 不能为空且不能是占位文本"
                if not item.get("source_data"):
                    return False, f"key_results[{index}] 为 verified 时 source_data 不能为空"
                if not str(item.get("code_cell", "")).strip() and not str(item.get("source", "")).strip():
                    return False, f"key_results[{index}] 为 verified 时 code_cell 或 source 至少一个非空"

    return True, ""
