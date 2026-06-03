"""Utilities for strict paper-figure artifact handling."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote


FIGURE_MANIFEST = "figure_artifacts.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
PAPER_FIGURE_DIRS = ("output/", "figures/", "final_results/")
DEBUG_FIGURE_DIR = "debug_artifacts/"

# Filenames that indicate test/placeholder figures — must never enter a paper.
PLACEHOLDER_FILENAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"test[_-]?", re.IGNORECASE),
    re.compile(r"demo[_-]?", re.IGNORECASE),
    re.compile(r"example[_-]?", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
    re.compile(r"sample[_-]?", re.IGNORECASE),
    re.compile(r"dummy[_-]?", re.IGNORECASE),
    re.compile(r"sine[_-]?", re.IGNORECASE),
    re.compile(r"正弦", re.IGNORECASE),
    re.compile(r"示例", re.IGNORECASE),
    re.compile(r"占位", re.IGNORECASE),
    re.compile(r"调试", re.IGNORECASE),
]

# Title/audit text substrings that indicate placeholder content.
PLACEHOLDER_TEXT_SUBSTRINGS: list[str] = [
    "test", "demo", "example", "placeholder", "sample", "dummy",
    "正弦", "示例", "占位", "待补", "TODO", "TBD",
]


def normalize_artifact_path(value: object) -> str:
    return unquote(str(value or "").strip()).replace("\\", "/").lstrip("./")


def is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def expected_language_aliases(document_language: str) -> set[str]:
    if document_language == "English":
        return {"english", "en", "en-us", "en_gb", "ascii english"}
    return {
        "simplified chinese",
        "chinese",
        "zh",
        "zh-cn",
        "zh_cn",
        "中文",
        "简体中文",
        "简体",
    }


def figure_artifact_path(item: object) -> str:
    if isinstance(item, dict):
        item = item.get("path") or item.get("filename") or item.get("file") or item.get("name") or ""
    return normalize_artifact_path(item)


def has_cjk_text(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def has_meaningful_latin_text(text: str) -> bool:
    allowed = {
        # Units
        "cm", "m", "s", "kg", "rad", "hz", "db", "si", "sic", "fp", "fft",
        "nm", "mm", "uv", "ir", "ml", "mg", "kv", "mw", "ghz", "mhz",
        "kpa", "mpa", "gpa", "nmm", "cms", "ms", "kmh", "rads",
        "rgb", "cmyk",
        # Math/statistics abbreviations
        "r", "r2", "aic", "bic", "rmse", "mae", "cos", "sin", "tan", "log",
        "ln", "pi", "max", "min", "avg", "std", "var", "sum",
    }
    words = re.findall(r"[A-Za-z]{2,}", text or "")
    return any(word.lower() not in allowed for word in words)


def truthy_metadata(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "yes", "1", "verified", "pass", "passed", "ok"}


def normalize_figure_artifact(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    path = figure_artifact_path(item)
    if not path or not is_image_path(path):
        return None
    normalized = dict(item)
    normalized["path"] = path
    normalized.setdefault("kind", "figure")
    return normalized


def is_verified_paper_figure(
    item: object,
    document_language: str,
    work_dir: str | Path | None = None,
    verified_result_ids: set[str] | None = None,
) -> bool:
    """Validate that an artifact is a legitimate paper-ready figure."""
    passed, _ = validate_figure(item, document_language, work_dir, verified_result_ids)
    return passed


# Trusted creators — figures from these sources are accepted.
TRUSTED_CREATERS = {"save_paper_figure", "deterministic_renderer"}



def validate_figure(
    item: object,
    document_language: str,
    work_dir: str | Path | None = None,
    verified_result_ids: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """Validate a figure artifact and return (passed, rejection_reasons).

    This is the SINGLE source of truth for figure validation.
    Both ``is_verified_paper_figure()`` and ``ArtifactRegistry.validate_for_paper()``
    must delegate to this function to prevent rule drift.

    Parameters
    ----------
    verified_result_ids:
        When provided, ``linked_result_ids`` on the artifact must be non-empty
        and every entry must exist in this set.  Pass ``None`` to skip the
        semantic-binding check.
    """
    artifact = normalize_figure_artifact(item)
    if artifact is None:
        return False, ["not_a_figure_artifact"]

    path = artifact["path"]
    reasons: list[str] = []

    if not path.startswith(PAPER_FIGURE_DIRS):
        reasons.append("path_not_in_paper_dir")
    if path.startswith(DEBUG_FIGURE_DIR):
        reasons.append("path_under_debug_artifacts")
    if artifact.get("kind") not in {None, "", "figure", "chart", "image"}:
        reasons.append("invalid_kind")
    if artifact.get("paper_ready") is not True:
        reasons.append("paper_ready_not_true")
    if artifact.get("chart_language_verified") is not True:
        reasons.append("chart_language_not_verified")

    # Trusted creator check
    creator = artifact.get("created_by")
    if creator not in TRUSTED_CREATERS:
        reasons.append(f"untrusted_creator:{creator}")
    helper_version = artifact.get("helper_version")
    if not isinstance(helper_version, (int, float)) or helper_version < 3:
        reasons.append("missing_or_old_helper_version")

    # Placeholder checks
    if artifact.get("is_placeholder") is True:
        reasons.append("is_placeholder_true")
    if is_placeholder_filename(path):
        reasons.append("placeholder_filename")
    audit_texts = artifact.get("visible_text_audit") or []
    if isinstance(audit_texts, list):
        combined_audit = " ".join(str(t) for t in audit_texts)
        if contains_placeholder_text(combined_audit):
            reasons.append("placeholder_text_in_audit")
        if not audit_texts:
            reasons.append("empty_visible_text_audit")
    else:
        reasons.append("visible_text_audit_not_list")

    # Language check (always enforced, regardless of verified_result_ids)
    language = str(
        artifact.get("visible_text_language")
        or artifact.get("chart_language")
        or artifact.get("language")
        or artifact.get("figure_language")
        or ""
    ).strip().lower()
    if not language:
        reasons.append("missing_visible_text_language")
    elif language not in expected_language_aliases(document_language):
        reasons.append("wrong_visible_text_language")

    audit = artifact.get("visible_text_audit")
    if not isinstance(audit, list) or not audit:
        reasons.append("missing_visible_text_audit")

    figure_kind = str(artifact.get("figure_kind") or "result_figure").strip().lower()

    # Semantic binding (only when verified_result_ids provided)
    if verified_result_ids is not None:
        if figure_kind == "explanatory_figure":
            binding = str(artifact.get("evidence_binding_type") or "model_contract").strip().lower()
            if binding != "model_contract":
                reasons.append("invalid_explanatory_binding_type")
            model_objects = artifact.get("model_objects")
            formulas = artifact.get("formulas")
            source_refs = artifact.get("source_problem_refs")
            source = artifact.get("source_data")
            has_model_evidence = (
                isinstance(model_objects, list) and any(str(item).strip() for item in model_objects)
            ) or (
                isinstance(formulas, list) and any(str(item).strip() for item in formulas)
            ) or (
                isinstance(source_refs, list) and any(str(item).strip() for item in source_refs)
            ) or (
                isinstance(source, list) and any(str(item).strip() for item in source)
            )
            if not has_model_evidence:
                reasons.append("missing_model_contract_evidence")
        else:
            linked = artifact.get("linked_result_ids")
            if not isinstance(linked, list) or not linked:
                reasons.append("missing_linked_result_ids")
            else:
                for rid in linked:
                    if str(rid) not in verified_result_ids:
                        reasons.append(f"linked_result_id_not_verified:{rid}")
            source = artifact.get("source_data")
            if not isinstance(source, list) or not source:
                reasons.append("missing_source_data")
            else:
                for entry in source:
                    if not str(entry).strip():
                        reasons.append("empty_source_data_entry")

    # File existence check
    if work_dir is not None and not (Path(work_dir) / path).exists():
        reasons.append("file_not_on_disk")

    return len(reasons) == 0, reasons


def is_placeholder_filename(path: str) -> bool:
    """Check whether a filename suggests test/demo/placeholder content."""
    name = Path(path).stem
    return any(pat.search(name) for pat in PLACEHOLDER_FILENAME_PATTERNS)


def contains_placeholder_text(text: str) -> bool:
    """Check whether text contains placeholder-related substrings."""
    lower = text.lower()
    return any(sub.lower() in lower for sub in PLACEHOLDER_TEXT_SUBSTRINGS)


def load_figure_manifest(work_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(work_dir) / FIGURE_MANIFEST
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        items = payload.get("figures", [])
    else:
        items = payload
    if not isinstance(items, list):
        return []
    return [artifact for artifact in (normalize_figure_artifact(item) for item in items) if artifact]


def save_figure_manifest(work_dir: str | Path, figures: list[dict[str, Any]]) -> None:
    path = Path(work_dir) / FIGURE_MANIFEST
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for figure in figures:
        artifact = normalize_figure_artifact(figure)
        if not artifact:
            continue
        marker = artifact["path"]
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(artifact)
    path.write_text(json.dumps({"figures": deduped}, ensure_ascii=False, indent=2), encoding="utf-8")

