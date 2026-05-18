"""Utilities for strict paper-figure artifact handling."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


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
    return str(value or "").strip().replace("\\", "/").lstrip("./")


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
    """Validate that an artifact is a legitimate paper-ready figure.

    Parameters
    ----------
    verified_result_ids:
        When provided, ``linked_result_ids`` on the artifact must be non-empty
        and every entry must exist in this set.  Pass ``None`` to skip the
        semantic-binding check (used by the per-file coder_agent guard which
        does not have access to the global result registry).
    """
    artifact = normalize_figure_artifact(item)
    if artifact is None:
        return False

    path = artifact["path"]
    if not path.startswith(PAPER_FIGURE_DIRS):
        return False
    if path.startswith(DEBUG_FIGURE_DIR):
        return False
    if artifact.get("kind") not in {None, "", "figure", "chart", "image"}:
        return False
    if artifact.get("paper_ready") is not True:
        return False
    if artifact.get("chart_language_verified") is not True:
        return False
    # All new paper figures MUST come from the save_paper_figure helper.
    # Old artifacts without helper_version/created_by are rejected — the
    # helper has been mandatory since helper_version 3.
    if artifact.get("created_by") != "save_paper_figure":
        return False
    helper_version = artifact.get("helper_version")
    if not isinstance(helper_version, (int, float)) or helper_version < 3:
        return False
    # Reject placeholder/test figures by filename or explicit flag.
    if artifact.get("is_placeholder") is True:
        return False
    if is_placeholder_filename(path):
        return False
    # Reject placeholder content in audit text.
    audit_texts = artifact.get("visible_text_audit") or []
    if isinstance(audit_texts, list):
        combined_audit = " ".join(str(t) for t in audit_texts)
        if contains_placeholder_text(combined_audit):
            return False

    language = str(
        artifact.get("visible_text_language")
        or artifact.get("chart_language")
        or artifact.get("language")
        or artifact.get("figure_language")
        or ""
    ).strip().lower()
    if language not in expected_language_aliases(document_language):
        return False

    audit = artifact.get("visible_text_audit")
    if not isinstance(audit, list) or not audit:
        return False

    # Semantic binding: linked_result_ids must reference verified results,
    # and source_data must be non-empty (proves what the figure is based on).
    if verified_result_ids is not None:
        linked = artifact.get("linked_result_ids")
        if not isinstance(linked, list) or not linked:
            return False
        for rid in linked:
            if str(rid) not in verified_result_ids:
                return False
        source = artifact.get("source_data")
        if not isinstance(source, list) or not source:
            return False
        for entry in source:
            if not str(entry).strip():
                return False

    if work_dir is not None and not (Path(work_dir) / path).exists():
        return False
    return True


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

