"""生成侧只声明来源；确定性物化为已有的逐值参数合同。"""

from __future__ import annotations

import itertools
import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.guidance.model_ir import IRModel, IndexedParameterSpec, IndexedParameterValue
from app.guidance.parameter_evidence import _fact_values, _payload, _table_values, table_column_cells


class FactValueBinding(IRModel):
    kind: Literal["fact"]
    fact_id: str = Field(min_length=1, max_length=120)


class TableColumnBinding(IRModel):
    kind: Literal["table_column"]
    source_ref: str = Field(min_length=1, max_length=250)
    column: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, min_length=1, max_length=80)


class TableRangeBinding(IRModel):
    kind: Literal["table_range"]
    source_ref: str = Field(min_length=1, max_length=250)
    row_start: int = Field(ge=0, le=10000, strict=True)
    row_stop: int = Field(ge=1, le=10001, strict=True)
    column_start: int = Field(ge=0, le=10000, strict=True)
    column_stop: int = Field(ge=1, le=10001, strict=True)
    order: Literal["row_major", "column_major"]
    unit: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_nonempty_range(self):
        if self.row_stop <= self.row_start or self.column_stop <= self.column_start:
            raise ValueError("table range must be nonempty and increasing")
        if (self.row_stop - self.row_start) * (self.column_stop - self.column_start) > 10000:
            raise ValueError("table range exceeds 10000 cells")
        return self


StaticParameterBinding = Annotated[
    FactValueBinding | TableColumnBinding | TableRangeBinding,
    Field(discriminator="kind"),
]


def materialize_static_parameter(parameter, *, index_sets, locked_facts, evidence_profile):
    domains = {item.id: item.values for item in index_sets}
    unknown = set(parameter.index_sets) - set(domains)
    if unknown:
        raise ValueError(f"unknown parameter index sets: {sorted(unknown)!r}")
    size = math.prod(len(domains[name]) for name in parameter.index_sets)
    if size > 10000:
        raise ValueError("static parameter exceeds 10000 values")
    coordinates = list(itertools.product(*(domains[name] for name in parameter.index_sets)))
    binding = parameter.binding
    fact_ids = list(parameter.source_fact_ids)
    if isinstance(binding, FactValueBinding):
        reference = f"problem_spec.json#locked_facts/{binding.fact_id}"
        value = _fact_values(reference, [_payload(fact) for fact in locked_facts or []])[0]
        fact_ids = list(dict.fromkeys([*fact_ids, binding.fact_id]))
        values = [IndexedParameterValue(coordinates=list(key), value=value, source_ref=reference) for key in coordinates]
    else:
        profile = _payload(evidence_profile) if evidence_profile is not None else {}
        tables = [table for table in profile.get("evidence", []) if table.get("source_ref") == binding.source_ref]
        if not tables:
            raise ValueError(f"unknown registered table source: {binding.source_ref!r}")
        if len(tables) != 1:
            raise ValueError("ambiguous registered table source")
        table = tables[0]
        try:
            if isinstance(binding, TableColumnBinding):
                cells = table_column_cells(table, binding.column, index_sets=parameter.index_sets, coordinates=coordinates)
            else:
                rows = table.get("rows") or []
                if binding.row_stop > len(rows) or any(
                    binding.column_stop > len(rows[row])
                    for row in range(binding.row_start, binding.row_stop)
                ):
                    raise ValueError("table range exceeds source bounds")
                cells = list(itertools.product(range(binding.row_start, binding.row_stop), range(binding.column_start, binding.column_stop)))
                if binding.order == "column_major":
                    cells.sort(key=lambda cell: (cell[1], cell[0]))
            if len(cells) != len(coordinates):
                raise ValueError(f"table selection has {len(cells)} cells for {len(coordinates)} parameter coordinates")
            values = []
            for key, (row, column) in zip(coordinates, cells):
                reference = f"{binding.source_ref}/rows/{row}/cells/{column}"
                value = _table_values(reference, tables, index_sets=parameter.index_sets, coordinates=[key], unit=binding.unit or "")[0]
                values.append(IndexedParameterValue(
                    coordinates=list(key), value=value, source_ref=reference, source_unit=binding.unit,
                ))
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"invalid table binding for parameter {parameter.id!r}: {exc}") from exc
    return IndexedParameterSpec(
        id=parameter.id,
        index_sets=parameter.index_sets,
        source_fact_ids=fact_ids,
        values=values,
    )
