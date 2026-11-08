"""子题事实快照与不可消耗的引用覆盖账本。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from typing import Any

from app.guidance.contracts import ProblemSpec


@dataclass(frozen=True)
class FactScope:
    subproblem_id: str
    fact_ids: tuple[str, ...]
    _snapshot: str = field(repr=False)

    @classmethod
    def from_problem_spec(cls, spec: ProblemSpec, subproblem_id: str) -> FactScope:
        target = next(item for item in spec.subproblems if item.id == subproblem_id)
        required_ids = set(target.required_fact_ids)
        facts = [
            fact.model_dump(mode="json")
            for fact in spec.locked_facts
            if fact.fact_id in required_ids
        ]
        return cls(
            subproblem_id=subproblem_id,
            fact_ids=tuple(fact["fact_id"] for fact in facts),
            _snapshot=json.dumps(facts, ensure_ascii=False),
        )

    def payload(self) -> list[dict[str, Any]]:
        # 每次提供独立副本，生成阶段和调用方均不能改写快照。
        return json.loads(self._snapshot)


@dataclass(frozen=True)
class FactCoverage:
    scope: FactScope
    _uses: tuple[tuple[str, str, tuple[str, ...]], ...] = ()

    def with_stage(
        self,
        stage: str,
        nodes: Iterable[tuple[str, Iterable[str]]],
    ) -> FactCoverage:
        uses = tuple((stage, node_id, tuple(fact_ids)) for node_id, fact_ids in nodes)
        unknown = sorted({
            fact_id for _, _, fact_ids in uses for fact_id in fact_ids
        } - set(self.scope.fact_ids))
        if unknown:
            raise ValueError(f"unknown locked fact ids in scope {self.scope.subproblem_id!r}: {unknown!r}")
        # 整阶段替换；失败尝试不修改已有覆盖，重试也不会累积旧引用。
        return FactCoverage(
            scope=self.scope,
            _uses=tuple(use for use in self._uses if use[0] != stage) + uses,
        )

    @property
    def uncovered_fact_ids(self) -> tuple[str, ...]:
        covered = {fact_id for _, _, ids in self._uses for fact_id in ids}
        return tuple(fact_id for fact_id in self.scope.fact_ids if fact_id not in covered)

    def require_complete(self) -> None:
        if self.uncovered_fact_ids:
            raise ValueError(
                f"fact coverage mismatch for {self.scope.subproblem_id!r}: "
                f"uncovered={list(self.uncovered_fact_ids)!r}"
            )

    def payload(self) -> dict[str, Any]:
        return {
            "required_fact_ids": list(self.scope.fact_ids),
            "uncovered_fact_ids": list(self.uncovered_fact_ids),
            "uses": [
                {"stage": stage, "node_id": node_id, "source_fact_ids": list(fact_ids)}
                for stage, node_id, fact_ids in self._uses
            ],
        }
