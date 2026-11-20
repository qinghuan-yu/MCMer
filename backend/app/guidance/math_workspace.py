"""子题的交互式建模草稿。所有工具只编辑候选，正式审核、求解和验证由工作流执行。"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from app.guidance.contracts import ProblemSpec
from app.guidance.fact_coverage import FactCoverage, FactScope
from app.guidance.generation_validation import (
    _validate_generation_draft_slice, _validate_indexed_constraint_stage, _validate_indexed_core_stage,
    _validate_indexed_parts, _validate_model_ir_slice,
)
from app.guidance.model_ir import (
    FactBindingSpec, IndexSetSpec, IndexedLinearExecutionComponent, IndexedVariableSpec, IRModel, ModelIR,
)
from app.guidance.model_ir_drafts import (
    FormulationContext, IndexedLinearConstraintDraft, IndexedLinearCoreDraft, IndexedLinearFormulationDraft,
    IndexedLinearObjectiveDraft, IndexedParameterDraft, ModelIRGenerationDraft, SolvableModelIRDraft,
)
from app.guidance.operators import OperatorRegistry
from app.guidance.parameter_evidence import _fact_values
from app.guidance.static_bindings import FactValueBinding


class _Variable(IndexedVariableSpec):
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)


class _Objective(IndexedLinearObjectiveDraft):
    source_fact_ids: list[str] = Field(default_factory=list, max_length=100)


Section = Literal["index_sets", "parameters", "additional_index_sets", "variables", "constraints"]
_COLLECTIONS = ("index_sets", "parameters", "additional_index_sets", "variables", "constraints")


class _Remove(IRModel):
    section: Section
    id: str = Field(min_length=1, max_length=100)


class _Draft(IRModel):
    architecture: ModelIRGenerationDraft | None = None
    index_sets: list[IndexSetSpec] = Field(default_factory=list, max_length=20)
    parameters: list[IndexedParameterDraft] = Field(default_factory=list, max_length=100)
    additional_index_sets: list[IndexSetSpec] = Field(default_factory=list, max_length=20)
    variables: list[_Variable] = Field(default_factory=list, max_length=100)
    objective: _Objective | None = None
    constraints: list[IndexedLinearConstraintDraft] = Field(default_factory=list, max_length=50)


class _Edit(_Draft):
    remove: list[_Remove] = Field(default_factory=list, max_length=300)


class _Inspect(IRModel):
    schema_section: Literal["architecture", "index_sets", "parameters", "additional_index_sets",
                            "variables", "objective", "constraints"] | None = Field(default=None, alias="schema")
    parameter_id: str | None = None
    offset: int = Field(default=0, ge=0)


class _Operators(IRModel):
    operator_ids: list[str] = Field(min_length=1, max_length=20)


class _NoArgs(IRModel):
    pass


def _function(name, description, schema):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


class MathWorkspace:
    def __init__(self, *, problem_spec: ProblemSpec, subproblem_id: str, evidence_profile: dict,
                 registry: OperatorRegistry):
        self.problem_spec = problem_spec
        self.subproblem_id = subproblem_id
        self.evidence_profile = evidence_profile
        self.registry = registry
        self.scope = FactScope.from_problem_spec(problem_spec, subproblem_id)
        self._draft = _Draft()
        self._coverage = FactCoverage(self.scope)
        self._core = IndexedLinearCoreDraft(index_sets=[]).promote_core()
        self._rejected_edit = False
        self.result: ModelIR | None = None

    @staticmethod
    def tools() -> list[dict]:
        # 只在模型查阅某个部分时展示完整嵌套合同，常驻工具给出操作及数学角色。
        descriptions = {
            "index_sets": "索引集，每项含 id, values。纯标量模型可省略。",
            "additional_index_sets": "边界条件和约束的辅助索引集，每项含 id, values。",
            "variables": "决策变量，每项含 id, domain(integer/binary/continuous)，可给出上下界、index_sets、source_fact_ids。",
            "parameters": "数值输入。每项含 id, source_kind, binding 或 runtime_source。fact 绑定必须提供可提取数值的事实。",
            "constraints": "每个约束含 id, terms, relation, rhs，可用 source_fact_ids 登记关系依据。",
            "remove": "按 section 和 id 删除集合项。",
        }
        fields = {name: {"type": "array", "items": {"type": "object"}, "description": desc}
                  for name, desc in descriptions.items()}
        fields["architecture"] = {"type": "object", "description": "ModelIRGenerationDraft，含 model_id 和一个 subproblems 条目。"}
        fields["objective"] = {"type": "object", "description": "含 sense、terms，可用 source_fact_ids 登记优化目标的依据。"}
        return [
            _function("inspect_model", "读取草稿和未覆盖事实。schema 指定字段时查看完整格式。parameter_id 可分页读取已物化数值。",
                      _Inspect.model_json_schema()),
            _function("inspect_operators", "读取已登记算子的输入输出合同。", _Operators.model_json_schema()),
            _function("edit_model", "原子编辑：集合按 id 新增或替换，单值字段整体替换。失败时可单独改正出错项。",
                      {"type": "object", "properties": fields, "additionalProperties": False, "minProperties": 1}),
            _function("submit_model", "检查全部数学结构、来源引用及覆盖范围，将候选交给独立审核。", _NoArgs.model_json_schema()),
        ]

    def execute(self, name: str, arguments: dict) -> dict:
        try:
            if name == "inspect_model":
                return {"ok": True, **self._inspect(_Inspect.model_validate(arguments))}
            if name == "inspect_operators":
                ids = _Operators.model_validate(arguments).operator_ids
                if len(set(ids)) != len(ids):
                    raise ValueError("operator_ids must be distinct")
                return {"ok": True, "contracts": self.registry.model_contracts(ids)}
            if self.result is not None:
                raise ValueError("the candidate has already been submitted")
            if name == "edit_model":
                self._rejected_edit = True
                changes = self._edit(_Edit.model_validate(arguments))
                self._rejected_edit = False
                return {"ok": True, **changes}
            if name == "submit_model":
                _NoArgs.model_validate(arguments)
                if self._rejected_edit:
                    raise ValueError("the last edit was rejected; correct it with edit_model before submitting")
                model = self._submit()
                self.result = model
                return {"ok": True, "model_id": model.model_id, "subproblem_id": self.subproblem_id,
                        "execution_status": model.subproblems[0].execution_status}
            raise ValueError(f"unknown modeling tool: {name!r}")
        except ValidationError as exc:
            issues = exc.errors(include_url=False, include_input=False)
            detail = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in issues[:6])
            return {"ok": False, "error": detail}
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            return {"ok": False, "error": str(exc)}

    def _inspect(self, request: _Inspect) -> dict:
        if request.schema_section is not None:
            annotation = _Draft.model_fields[request.schema_section].annotation
            return {"schema": TypeAdapter(annotation).json_schema()}
        if request.parameter_id is not None:
            parameter = next((p for p in self._core.parameters if p.id == request.parameter_id), None)
            if parameter is None:
                raise ValueError(f"unknown parameter: {request.parameter_id!r}")
            return {"parameter": self._parameter_page(parameter, request.offset)}
        return {"draft": self._draft.model_dump(mode="json"),
                "fact_coverage": self._coverage.payload()}

    @staticmethod
    def _parameter_page(parameter, offset=0, count=32):
        data = parameter.model_dump(mode="json", exclude={"values", "dense_values"}, exclude_none=True)
        data["values"] = [v.model_dump(mode="json", exclude_none=True) for v in parameter.values[offset:offset + count]]
        data["total_values"] = len(parameter.values)
        data["offset"] = offset
        return data

    def _edit(self, edit: _Edit) -> dict:
        if not any(getattr(edit, field) for field in edit.model_fields_set):
            raise ValueError("edit_model requires a nonempty edit")
        draft = self._draft.model_dump(mode="json")
        patch = edit.model_dump(mode="json", exclude_unset=True)
        # 输入中的字符串也可能被数值字段转换为 NaN；所有工具回执必须能作为严格 JSON 传输。
        try:
            json.dumps(patch, allow_nan=False)
        except ValueError as exc:
            raise ValueError(f"{', '.join(patch)}: numbers must be finite; use null for an unbounded variable") from exc
        changed = {}
        for item in patch.pop("remove", []):
            section, item_id = item["section"], item["id"]
            remaining = [entry for entry in draft[section] if entry["id"] != item_id]
            if len(remaining) == len(draft[section]):
                raise ValueError(f"{section}: unknown id {item_id!r}")
            draft[section] = remaining
            changed.setdefault(section, []).append(item_id)
        for section, value in patch.items():
            if section not in _COLLECTIONS:
                draft[section] = value
                changed[section] = True
                continue
            indexed = {entry["id"]: entry for entry in draft[section]}
            ids = [entry["id"] for entry in value]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{section}: duplicate id in edit")
            indexed.update((entry["id"], entry) for entry in value)
            draft[section] = list(indexed.values())
            changed.setdefault(section, []).extend(ids)

        updated = _Draft.model_validate(draft)
        architecture = updated.architecture
        coverage = (_validate_generation_draft_slice(architecture, expected_id=self.subproblem_id,
                    registry=self.registry, problem_spec=self.problem_spec)
                    if architecture else FactCoverage(self.scope))
        if architecture is not None and not self._is_indexed(architecture):
            if any(getattr(updated, field) for field in (*_COLLECTIONS, "objective")):
                raise ValueError("indexed fields require the indexed_linear_model execution component")
        core = self._materialize(updated, coverage)
        coverage = coverage.with_stage("parameters", ((p.id, p.source_fact_ids) for p in core.parameters))
        structural = [(v.id, v.source_fact_ids) for v in updated.variables]
        if updated.objective is not None:
            structural.append(("objective", updated.objective.source_fact_ids))
        coverage = coverage.with_stage("structure", structural)
        coverage = coverage.with_stage("indexed_constraints", ((c.id, c.source_fact_ids) for c in updated.constraints))
        self._check_references(updated)
        if updated.variables and updated.objective is not None and updated.constraints:
            _validate_indexed_parts(core, self._formulation(updated), FormulationContext.from_core(core))

        # 所有读取、物化、语义检查成功后才切换草稿，失败期间已接受的编辑不受影响。
        self._draft, self._core, self._coverage = updated, core, coverage
        ids = set(changed.get("parameters", []))
        observed = [self._parameter_page(p, count=8) for p in core.parameters if p.id in ids or "index_sets" in changed]
        return {"changed": changed, "parameters": observed, "uncovered_fact_ids": list(coverage.uncovered_fact_ids)}

    @staticmethod
    def _check_references(draft):
        variables = {v.id for v in draft.variables}
        parameters = {p.id for p in draft.parameters}
        axes = {s.id for s in [*draft.index_sets, *draft.additional_index_sets]}
        for variable in draft.variables:
            unknown = set(variable.index_sets) - axes
            if unknown:
                raise ValueError(f"variables.{variable.id}.index_sets: unknown index sets {sorted(unknown)!r}")
        expressions = [(f"constraints.{row.id}", row.terms, [row.rhs]) for row in draft.constraints]
        if draft.objective:
            expressions.append(("objective", draft.objective.terms, []))
        for field, terms, rhs in expressions:
            unknown = {term.variable.variable_id for term in terms} - variables
            if unknown:
                raise ValueError(f"{field}: declare variables {sorted(unknown)!r} before referencing them")
            values = [*rhs, *(term.coefficient for term in terms)]
            unknown = {value.parameter_id for value in values if value.kind == "parameter"} - parameters
            if unknown:
                raise ValueError(f"{field}: bind parameters {sorted(unknown)!r} before referencing them")

    def _materialize(self, draft, coverage):
        facts = self.scope.payload()
        for parameter in draft.parameters:
            binding = getattr(parameter, "binding", None)
            if isinstance(binding, FactValueBinding):
                try:
                    _fact_values(f"problem_spec.json#locked_facts/{binding.fact_id}", facts)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise ValueError(
                        f"parameters.{parameter.id}.binding: {binding.fact_id!r} must refer to a scoped numeric fact. "
                        "Declare decisions and integer domains in variables, optimization sense in objective, "
                        "and inequalities in constraints. Cite structural facts with source_fact_ids there."
                    ) from exc
        data = IndexedLinearCoreDraft(index_sets=draft.index_sets, parameters=draft.parameters)
        return _validate_indexed_core_stage(data, coverage, evidence_profile=self.evidence_profile)

    @staticmethod
    def _is_indexed(architecture):
        sub = architecture.subproblems[0]
        return isinstance(sub, SolvableModelIRDraft) and "indexed_linear_model" in sub.execution_components

    @staticmethod
    def _formulation(draft):
        return IndexedLinearFormulationDraft.model_validate({
            "additional_index_sets": draft.additional_index_sets,
            "variables": [v.model_dump(exclude={"source_fact_ids"}) for v in draft.variables],
            "objective": draft.objective.model_dump(exclude={"source_fact_ids"}) if draft.objective else None,
            "constraints": draft.constraints,
        })

    def _submit(self):
        draft = self._draft
        if draft.architecture is None:
            raise ValueError("architecture: declare the modeling route and expected outputs before submitting")
        if self._is_indexed(draft.architecture):
            formulation = self._formulation(draft)
            _validate_indexed_constraint_stage(core=self._core, constraints=formulation,
                                               context=FormulationContext.from_core(self._core), coverage=self._coverage)
            model = draft.architecture.promote(formulation.promote(self._core))
            sub = model.subproblems[0]
            idx = next(i for i, comp in enumerate(sub.execution_components) if isinstance(comp, IndexedLinearExecutionComponent))
            # 形式化模型已有事实→JSON Pointer 合同，变量域和目标事实也落到实际模型字段。
            paths = {b.fact_id: list(b.model_paths) for b in sub.fact_bindings}
            owners = [(f"variables/{i}", v.source_fact_ids) for i, v in enumerate(draft.variables)]
            owners.append(("objective", draft.objective.source_fact_ids))
            for path, ids in owners:
                for fact_id in ids:
                    paths.setdefault(fact_id, []).append(f"/execution_components/{idx}/model/{path}")
            sub.fact_bindings = [FactBindingSpec(fact_id=fact_id, model_paths=links) for fact_id, links in paths.items()]
            model = ModelIR.model_validate(model.model_dump(mode="python"))
        else:
            model = draft.architecture.promote()
        _validate_model_ir_slice(model, expected_id=self.subproblem_id, problem_spec=self.problem_spec,
                                 evidence_profile=self.evidence_profile, registry=self.registry)
        return model
