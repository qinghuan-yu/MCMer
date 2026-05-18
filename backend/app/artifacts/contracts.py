"""ProblemContract — immutable language and requirement contract for a task.

Generated once at task initialization. All downstream agents and tools
read this contract instead of re-detecting language or inferring figure needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProblemContract:
    """Immutable contract describing the problem's language and output requirements.

    Created once at task initialization. Written to ``problem_contract.json``
    in the work directory so every agent/tool can read it.
    """

    document_language: str  # "Simplified Chinese" or "English"
    chart_language: str  # "Simplified Chinese" or "English"
    allowed_visible_text_language: str  # "zh-CN" or "en"
    font_profile: str  # "cjk" or "latin"
    paper_requires_figures: bool
    paper_requires_tables: bool
    workflow_mode: str  # "fast", "standard", "strict"
    chart_language_aliases: list[str] = field(default_factory=list)
    allowed_latin_tokens: list[str] = field(default_factory=list)

    # ── Serialisation ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, work_dir: str | Path) -> Path:
        path = Path(work_dir) / "problem_contract.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    # ── Construction helpers ───────────────────────────────────────────

    @classmethod
    def from_question(
        cls,
        question: str,
        solve_spec: dict[str, Any] | None = None,
        workflow_mode: str = "standard",
    ) -> ProblemContract:
        """Build a contract from the question text and optional solve_spec."""
        from app.core.workflow import _detect_document_language, _solve_spec_expects_figures

        chart_language = _detect_document_language(question)

        if chart_language == "English":
            aliases = ["english", "en", "en-us", "en_gb", "ascii english"]
            font_profile = "latin"
            allowed_text_lang = "en"
        else:
            aliases = ["simplified chinese", "chinese", "zh", "zh-cn", "zh_cn", "中文", "简体中文", "简体"]
            font_profile = "cjk"
            allowed_text_lang = "zh-CN"

        requires_figures = _solve_spec_expects_figures(
            solve_spec or {}, question, ""
        )

        # Heuristic: if the question mentions tables/表格
        requires_tables = bool(
            __import__("re").search(
                r"(?:表格|表\s*\d|table\s*\d|数据表|汇总表)", question, re.IGNORECASE
            )
        ) if question else False

        # Allowed latin tokens for Chinese charts (units, variables, math)
        latin_tokens = [
            "cm", "m", "s", "kg", "rad", "hz", "db", "si", "sic", "fp", "fft",
            "nm", "mm", "uv", "ir", "ml", "mg", "kv", "mw", "ghz", "mhz",
            "kpa", "mpa", "gpa", "nmm", "cms", "ms", "kmh", "rads",
            "rgb", "cmyk", "r", "r2", "aic", "bic", "rmse", "mae",
            "cos", "sin", "tan", "log", "ln", "pi", "max", "min", "avg",
            "std", "var", "sum",
        ]

        return cls(
            document_language=chart_language,
            chart_language=chart_language,
            allowed_visible_text_language=allowed_text_lang,
            font_profile=font_profile,
            paper_requires_figures=requires_figures,
            paper_requires_tables=requires_tables,
            workflow_mode=workflow_mode,
            chart_language_aliases=aliases,
            allowed_latin_tokens=latin_tokens,
        )

    @classmethod
    def load(cls, work_dir: str | Path) -> ProblemContract | None:
        """Load a previously saved contract, or None if not found."""
        path = Path(work_dir) / "problem_contract.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None

    # ── Convenience properties ─────────────────────────────────────────

    @property
    def is_chinese(self) -> bool:
        return self.chart_language == "Simplified Chinese"

    @property
    def is_english(self) -> bool:
        return self.chart_language == "English"

    @property
    def chart_language_context(self) -> dict[str, Any]:
        """Return the chart_language_context dict used by prompts."""
        if self.is_english:
            return {
                "document_language": "English",
                "chart_language": "English",
                "required_visible_text": "English",
                "final_image_contract": {
                    "paper_ready": True,
                    "visible_text_language": "English",
                    "chart_language_verified": True,
                    "is_placeholder": False,
                    "figure_role": "result_summary",
                    "linked_result_ids": [],
                    "source_data": [],
                },
                "rules": [
                    "All visible chart text must be English.",
                    "Do not cite or register Chinese-labeled figures as paper_ready.",
                    "Use save_paper_figure for final figures.",
                    "Every figure must have linked_result_ids referencing verified results.",
                    "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
                ],
            }
        return {
            "document_language": "Simplified Chinese",
            "chart_language": "Simplified Chinese",
            "required_visible_text": "Simplified Chinese",
            "font_policy": "Use container Chinese fonts configured by LocalCodeInterpreter.",
            "final_image_contract": {
                "paper_ready": True,
                "visible_text_language": "Simplified Chinese",
                "chart_language_verified": True,
                "is_placeholder": False,
                "figure_role": "result_summary",
                "linked_result_ids": [],
                "source_data": [],
            },
            "rules": [
                "All visible chart text must be Simplified Chinese.",
                "English is allowed only for units, formulas, and established symbols.",
                "Use save_paper_figure for final figures.",
                "Every figure must have linked_result_ids referencing verified results.",
                "Placeholder/test figures (test_plot.png, demo.png, etc.) are forbidden.",
            ],
        }
