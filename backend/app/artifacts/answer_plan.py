"""Answer slot planning and result completeness diagnostics.

This module keeps the "what must be answered" contract separate from writer
style.  It derives a small deterministic answer plan from ``solve_spec`` and
checks whether ``result_registry`` contains verified evidence for each slot.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.utils.common_utils import save_json


_TABLE_HINTS = ("table", "fill", "填写", "填充", "表格", "附表", "结果表")
_NUMERIC_HINTS = (
    "calculate",
    "compute",
    "estimate",
    "predict",
    "optimize",
    "parameter",
    "value",
    "求",
    "计算",
    "估计",
    "预测",
    "优化",
    "参数",
    "数值",
    "结果",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value).strip("_")
    return normalized[:80] or fallback


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints)


def _result_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "section", "problem_section", "name", "claim_text", "result_type", "source_file"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


@dataclass(frozen=True)
class AnswerSlot:
    id: str
    subproblem_id: str
    description: str
    required_outputs: list[str] = field(default_factory=list)
    requires_numeric_answer: bool = True
    requires_table_answer: bool = False
    source: str = "solve_spec"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnswerPlanBuilder:
    """Derive answer slots from solve_spec and compare them with results."""

    @staticmethod
    def _subproblems(solve_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(solve_spec, dict):
            return []
        raw = solve_spec.get("subproblems") or solve_spec.get("problems") or []
        return [item for item in _as_list(raw) if isinstance(item, dict)]

    @staticmethod
    def _required_outputs(subproblem: dict[str, Any]) -> list[str]:
        outputs: list[str] = []
        for key in (
            "expected_outputs",
            "outputs",
            "deliverables",
            "required_results",
            "answer_requirements",
            "tables_to_fill",
        ):
            for item in _as_list(subproblem.get(key)):
                if isinstance(item, dict):
                    text = _clean(item.get("name") or item.get("description") or item.get("id"))
                else:
                    text = _clean(item)
                if text and text not in outputs:
                    outputs.append(text)
        return outputs

    @staticmethod
    def build_slots(
        *,
        solve_spec: dict[str, Any] | None,
        question: str = "",
    ) -> list[AnswerSlot]:
        slots: list[AnswerSlot] = []
        subproblems = AnswerPlanBuilder._subproblems(solve_spec)
        if not subproblems and _clean(question):
            subproblems = [{"id": "global", "objective": question, "source": "question"}]

        for index, subproblem in enumerate(subproblems, start=1):
            subproblem_id = _clean(subproblem.get("id") or subproblem.get("subproblem_id") or f"q{index}")
            description = _clean(
                subproblem.get("objective")
                or subproblem.get("question")
                or subproblem.get("description")
                or subproblem.get("task")
                or subproblem_id
            )
            outputs = AnswerPlanBuilder._required_outputs(subproblem)
            combined = " ".join([
                description,
                " ".join(outputs),
                _clean(subproblem.get("method")),
                _clean(subproblem.get("notes")),
            ])
            requires_table = bool(subproblem.get("requires_table_answer")) or _contains_any(combined, _TABLE_HINTS)
            requires_numeric = (
                subproblem.get("requires_numeric_answer") is not False
                and (
                    bool(outputs)
                    or requires_table
                    or _contains_any(combined, _NUMERIC_HINTS)
                    or subproblem_id.lower().startswith(("q", "p", "problem"))
                )
            )
            slot_id = _slug(f"{subproblem_id}_answer", f"q{index}_answer")
            slots.append(
                AnswerSlot(
                    id=slot_id,
                    subproblem_id=subproblem_id,
                    description=description,
                    required_outputs=outputs or [description],
                    requires_numeric_answer=requires_numeric,
                    requires_table_answer=requires_table,
                    source=_clean(subproblem.get("source")) or "solve_spec",
                )
            )
        return slots

    @staticmethod
    def _matches_slot(result: dict[str, Any], slot: AnswerSlot) -> bool:
        text = _result_text(result)
        sid = slot.subproblem_id.lower()
        if not sid or sid == "global":
            return True
        aliases = {
            sid,
            sid.replace("_", ""),
            sid.replace("-", ""),
            f"problem {sid.lstrip('q')}",
            f"problem{sid.lstrip('q')}",
            f"第{sid.lstrip('q')}问" if sid.lower().startswith("q") else sid,
        }
        return any(alias and alias in text for alias in aliases)

    @staticmethod
    def evaluate(
        *,
        solve_spec: dict[str, Any] | None,
        result_registry: dict[str, Any] | None,
        question: str = "",
    ) -> dict[str, Any]:
        slots = AnswerPlanBuilder.build_slots(solve_spec=solve_spec, question=question)
        registry = result_registry if isinstance(result_registry, dict) else {}
        verified = [item for item in _as_list(registry.get("verified_results")) if isinstance(item, dict)]
        blocked = [item for item in _as_list(registry.get("blocked_results")) if isinstance(item, dict)]

        slot_payloads: list[dict[str, Any]] = []
        covered = 0
        blocked_count = 0
        for slot in slots:
            verified_matches = [item.get("id") for item in verified if AnswerPlanBuilder._matches_slot(item, slot)]
            blocked_matches = [item.get("id") for item in blocked if AnswerPlanBuilder._matches_slot(item, slot)]
            if verified_matches:
                status = "covered"
                covered += 1
            elif blocked_matches:
                status = "blocked"
                blocked_count += 1
            else:
                status = "missing"
            slot_payloads.append({
                **slot.to_dict(),
                "status": status,
                "verified_result_ids": [str(item) for item in verified_matches if item],
                "blocked_result_ids": [str(item) for item in blocked_matches if item],
            })

        missing_count = len(slots) - covered - blocked_count
        if not slots:
            coverage_status = "not_required"
        elif covered == len(slots):
            coverage_status = "ready"
        elif covered:
            coverage_status = "partial"
        elif blocked_count:
            coverage_status = "blocked"
        else:
            coverage_status = "missing"

        return {
            "planner": "AnswerPlanBuilder",
            "slot_count": len(slots),
            "covered_count": covered,
            "blocked_count": blocked_count,
            "missing_count": missing_count,
            "coverage_status": coverage_status,
            "slots": slot_payloads,
        }

    @staticmethod
    def save(work_dir: str | Path, plan: dict[str, Any]) -> Path:
        path = Path(work_dir) / "answer_table_plan.json"
        save_json(plan, str(path))
        return path
