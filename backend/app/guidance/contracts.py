"""File contracts for the high-trust guidance lifecycle."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str
    status: Literal["available", "missing"]
    matched_files: list[str] = Field(default_factory=list)
    note: str


ScalarValue = bool | int | float | str


class LockedFactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$", max_length=120)
    statement: str = Field(min_length=1, max_length=800)
    source_refs: list[str] = Field(min_length=1, max_length=20)
    subproblem_ids: list[str] = Field(min_length=1, max_length=50)
    semantic_key: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
        max_length=160,
    )
    value: ScalarValue
    unit: str = Field(max_length=100)


class UncertaintySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uncertainty_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$", max_length=120)
    statement: str = Field(min_length=1, max_length=800)
    source_refs: list[str] = Field(min_length=1, max_length=20)
    subproblem_ids: list[str] = Field(min_length=1, max_length=50)
    semantic_key: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
        max_length=160,
    )
    candidate_values: list[ScalarValue] = Field(default_factory=list, max_length=20)
    unit: str = Field(max_length=100)


class LockedFactGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_id: str = Field(min_length=1, max_length=400)
    statement: str = Field(min_length=1, max_length=800)
    source_refs: list[str] = Field(min_length=1, max_length=20)
    subproblem_ids: list[str] = Field(min_length=1, max_length=50)
    semantic_key: str = Field(min_length=1, max_length=400)
    value: ScalarValue
    unit: str = Field(max_length=100)


class UncertaintyGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uncertainty_id: str = Field(min_length=1, max_length=400)
    statement: str = Field(min_length=1, max_length=800)
    source_refs: list[str] = Field(min_length=1, max_length=20)
    subproblem_ids: list[str] = Field(min_length=1, max_length=50)
    semantic_key: str = Field(min_length=1, max_length=400)
    candidate_values: list[ScalarValue] = Field(default_factory=list, max_length=20)
    unit: str = Field(max_length=100)


class SubproblemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=500)
    objective: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=1000)
    brief_description: str = Field(default="", max_length=1000)
    goals: list[str] = Field(default_factory=list, max_length=50)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    outputs: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    candidate_directions: list[str] = Field(default_factory=list, max_length=50)
    problem_type: str = Field(default="", max_length=200)
    required_fact_ids: list[str] = Field(min_length=0, max_length=200)
    required_uncertainty_ids: list[str] = Field(min_length=0, max_length=100)
    depends_on_subproblem_ids: list[str] = Field(min_length=0, max_length=50)

    @model_validator(mode="after")
    def require_unique_references(self) -> SubproblemSpec:
        for field_name in (
            "required_fact_ids",
            "required_uncertainty_ids",
            "depends_on_subproblem_ids",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique references")
        return self


class ProblemDataSourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    source_ref: str = Field(min_length=1, max_length=500)


class ProblemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    task_summary: str
    subproblems: list[SubproblemSpec] = Field(default_factory=list)
    data_sources: list[ProblemDataSourceSpec] = Field(default_factory=list)
    source_requirements: list[SourceRequirement] = Field(default_factory=list)
    locked_facts: list[LockedFactSpec] = Field(default_factory=list)
    uncertainties: list[UncertaintySpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def assign_stable_subproblem_ids(self) -> ProblemSpec:
        normalized: list[SubproblemSpec] = []
        for index, subproblem in enumerate(self.subproblems, start=1):
            subproblem_id = canonical_subproblem_id(subproblem.id) or f"problem{index}"
            normalized.append(subproblem.model_copy(update={
                "id": subproblem_id,
                "depends_on_subproblem_ids": [
                    canonical_subproblem_id(item)
                    for item in subproblem.depends_on_subproblem_ids
                ],
            }))
        self.subproblems = normalized
        valid_ids = {item.id for item in normalized}
        for record in [*self.locked_facts, *self.uncertainties]:
            record.subproblem_ids = list(dict.fromkeys(
                canonical_subproblem_id(item) for item in record.subproblem_ids
            ))
            unknown = sorted(set(record.subproblem_ids) - valid_ids)
            if unknown:
                raise ValueError(f"fact scope references unknown subproblem ids: {unknown!r}")

        fact_ids = [item.fact_id for item in self.locked_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("locked fact ids must be unique")
        uncertainty_ids = [item.uncertainty_id for item in self.uncertainties]
        if len(uncertainty_ids) != len(set(uncertainty_ids)):
            raise ValueError("uncertainty ids must be unique")
        data_source_ids = [item.id for item in self.data_sources]
        if len(data_source_ids) != len(set(data_source_ids)):
            raise ValueError("data source ids must be unique")
        structured_source_refs = {
            source_ref
            for record in [*self.locked_facts, *self.uncertainties]
            for source_ref in record.source_refs
        }
        uncovered_data_sources = [
            item.id
            for item in self.data_sources
            if item.source_ref not in structured_source_refs
        ]
        if uncovered_data_sources:
            raise ValueError(
                "data source evidence coverage mismatch: "
                f"{uncovered_data_sources!r}"
            )

        facts_by_id = {item.fact_id: item for item in self.locked_facts}
        uncertainties_by_id = {
            item.uncertainty_id: item for item in self.uncertainties
        }
        for subproblem in self.subproblems:
            unknown_facts = sorted(
                set(subproblem.required_fact_ids) - set(facts_by_id)
            )
            if unknown_facts:
                raise ValueError(f"unknown required fact ids: {unknown_facts!r}")
            invalid_fact_scope = [
                fact_id
                for fact_id in subproblem.required_fact_ids
                if subproblem.id not in facts_by_id[fact_id].subproblem_ids
            ]
            if invalid_fact_scope:
                raise ValueError(
                    "fact reference scope mismatch for "
                    f"{subproblem.id!r}: {invalid_fact_scope!r}"
                )
            expected_facts = [
                item.fact_id
                for item in self.locked_facts
                if subproblem.id in item.subproblem_ids
            ]
            if subproblem.required_fact_ids != expected_facts:
                raise ValueError(
                    "fact reference coverage mismatch for "
                    f"{subproblem.id!r}: expected={expected_facts!r}; "
                    f"referenced={subproblem.required_fact_ids!r}"
                )

            unknown_uncertainties = sorted(
                set(subproblem.required_uncertainty_ids)
                - set(uncertainties_by_id)
            )
            if unknown_uncertainties:
                raise ValueError(
                    "unknown required uncertainty ids: "
                    f"{unknown_uncertainties!r}"
                )
            invalid_uncertainty_scope = [
                uncertainty_id
                for uncertainty_id in subproblem.required_uncertainty_ids
                if (
                    subproblem.id
                    not in uncertainties_by_id[uncertainty_id].subproblem_ids
                )
            ]
            if invalid_uncertainty_scope:
                raise ValueError(
                    "uncertainty reference scope mismatch for "
                    f"{subproblem.id!r}: {invalid_uncertainty_scope!r}"
                )
            expected_uncertainties = [
                item.uncertainty_id
                for item in self.uncertainties
                if subproblem.id in item.subproblem_ids
            ]
            if subproblem.required_uncertainty_ids != expected_uncertainties:
                raise ValueError(
                    "uncertainty reference coverage mismatch for "
                    f"{subproblem.id!r}: expected={expected_uncertainties!r}; "
                    f"referenced={subproblem.required_uncertainty_ids!r}"
                )

            unknown_dependencies = sorted(
                set(subproblem.depends_on_subproblem_ids) - valid_ids
            )
            if unknown_dependencies:
                raise ValueError(
                    "unknown subproblem dependency ids: "
                    f"{unknown_dependencies!r}"
                )
            if subproblem.id in subproblem.depends_on_subproblem_ids:
                raise ValueError(
                    f"subproblem dependency cycle includes {subproblem.id!r}"
                )

        dependencies = {
            item.id: item.depends_on_subproblem_ids
            for item in self.subproblems
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(subproblem_id: str) -> None:
            if subproblem_id in visiting:
                raise ValueError(
                    f"subproblem dependency cycle includes {subproblem_id!r}"
                )
            if subproblem_id in visited:
                return
            visiting.add(subproblem_id)
            for dependency_id in dependencies[subproblem_id]:
                visit(dependency_id)
            visiting.remove(subproblem_id)
            visited.add(subproblem_id)

        for subproblem_id in dependencies:
            visit(subproblem_id)

        for index, left in enumerate(self.locked_facts):
            for right in self.locked_facts[index + 1:]:
                if left.semantic_key != right.semantic_key:
                    continue
                if not set(left.subproblem_ids).intersection(right.subproblem_ids):
                    continue
                left_value = (type(left.value).__name__, left.value, left.unit)
                right_value = (type(right.value).__name__, right.value, right.unit)
                if left_value != right_value:
                    raise ValueError(
                        "conflicting locked facts for semantic key "
                        f"{left.semantic_key!r}: {left.fact_id!r} != {right.fact_id!r}"
                    )
        return self


def canonical_subproblem_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?i)(?:problem|prob|p|subproblem|subproblem_)?\s*[_-]?\s*(\d+)", text)
    if match:
        return f"problem{int(match.group(1))}"
    return text


class SubproblemStructureGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=500)
    objective: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=1000)
    brief_description: str = Field(default="", max_length=1000)
    goals: list[str] = Field(default_factory=list, max_length=50)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    outputs: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    candidate_directions: list[str] = Field(default_factory=list, max_length=50)
    problem_type: str = Field(default="", max_length=200)
    depends_on_subproblem_ids: list[str] = Field(min_length=0, max_length=50)


class ProblemFactsGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked_facts: list[LockedFactGenerationDraft] = Field(default_factory=list)
    uncertainties: list[UncertaintyGenerationDraft] = Field(default_factory=list)


class ProblemStructureGenerationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    task_summary: str
    subproblems: list[SubproblemStructureGenerationDraft] = Field(
        default_factory=list
    )
    data_sources: list[ProblemDataSourceSpec] = Field(default_factory=list)
    source_requirements: list[SourceRequirement] = Field(default_factory=list)

    def promote(self, facts: ProblemFactsGenerationDraft) -> ProblemSpec:
        fact_ids = _promote_identifier_sequence(
            [item.fact_id for item in facts.locked_facts],
            field_name="fact_id",
            fallback_prefix="fact",
            max_length=120,
        )
        uncertainty_ids = _promote_identifier_sequence(
            [item.uncertainty_id for item in facts.uncertainties],
            field_name="uncertainty_id",
            fallback_prefix="uncertainty",
            max_length=120,
        )
        semantic_keys = _promote_identifier_sequence(
            [
                *[item.semantic_key for item in facts.locked_facts],
                *[item.semantic_key for item in facts.uncertainties],
            ],
            field_name="semantic_key",
            fallback_prefix="semantic",
            max_length=160,
            allow_repeated_source=True,
        )
        locked_facts = [
            LockedFactSpec.model_validate({
                **item.model_dump(mode="python"),
                "fact_id": fact_ids[index],
                "semantic_key": semantic_keys[index],
                "subproblem_ids": [
                    _promote_subproblem_identifier(value)
                    for value in item.subproblem_ids
                ],
            })
            for index, item in enumerate(facts.locked_facts)
        ]
        uncertainty_offset = len(facts.locked_facts)
        uncertainties = [
            UncertaintySpec.model_validate({
                **item.model_dump(mode="python"),
                "uncertainty_id": uncertainty_ids[index],
                "semantic_key": semantic_keys[uncertainty_offset + index],
                "subproblem_ids": [
                    _promote_subproblem_identifier(value)
                    for value in item.subproblem_ids
                ],
            })
            for index, item in enumerate(facts.uncertainties)
        ]
        subproblems = _promote_subproblems(self.subproblems)
        subproblems = [
            item.model_copy(update={
                "required_fact_ids": [
                    fact.fact_id
                    for fact in locked_facts
                    if item.id in fact.subproblem_ids
                ],
                "required_uncertainty_ids": [
                    uncertainty.uncertainty_id
                    for uncertainty in uncertainties
                    if item.id in uncertainty.subproblem_ids
                ],
            })
            for item in subproblems
        ]
        return ProblemSpec(
            schema_version=self.schema_version,
            task_summary=self.task_summary,
            subproblems=subproblems,
            data_sources=self.data_sources,
            source_requirements=self.source_requirements,
            locked_facts=locked_facts,
            uncertainties=uncertainties,
        )


def _promote_subproblems(
    subproblems: list[SubproblemStructureGenerationDraft],
) -> list[SubproblemSpec]:
    promoted: list[SubproblemSpec] = []
    sources_by_identifier: dict[str, str] = {}
    identifiers_by_source: dict[str, str] = {}
    for index, subproblem in enumerate(subproblems, start=1):
        source = unicodedata.normalize(
            "NFKC",
            subproblem.id,
        ).strip()
        identifier = (
            _promote_subproblem_identifier(source)
            if source
            else f"problem{index}"
        )
        previous_source = sources_by_identifier.get(identifier)
        if previous_source is not None:
            raise ValueError(
                "subproblem_id normalization collision: "
                f"{previous_source!r} and {source!r} -> {identifier!r}"
            )
        sources_by_identifier[identifier] = source
        identifiers_by_source[source] = identifier
    for subproblem in subproblems:
        source = unicodedata.normalize("NFKC", subproblem.id).strip()
        promoted.append(SubproblemSpec.model_validate({
            **subproblem.model_dump(mode="python"),
            "id": identifiers_by_source[source],
            "required_fact_ids": [],
            "required_uncertainty_ids": [],
            "depends_on_subproblem_ids": [
                identifiers_by_source.get(
                    unicodedata.normalize("NFKC", item).strip(),
                    _promote_subproblem_identifier(item),
                )
                for item in subproblem.depends_on_subproblem_ids
            ],
        }))
    return promoted


def _promote_subproblem_identifier(value: Any) -> str:
    source = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = re.fullmatch(
        r"(?i)(?:problem|prob|p|q|question|subproblem|subproblem_)?"
        r"\s*[_-]?\s*(\d+)",
        source,
    )
    if match:
        return f"problem{int(match.group(1))}"
    return _promote_generation_identifier(
        source,
        fallback_prefix="problem",
        max_length=120,
    )


def _promote_identifier_sequence(
    values: list[str],
    *,
    field_name: str,
    fallback_prefix: str,
    max_length: int,
    allow_repeated_source: bool = False,
) -> list[str]:
    promoted: list[str] = []
    sources_by_identifier: dict[str, str] = {}
    for value in values:
        normalized_source = unicodedata.normalize("NFKC", value).strip()
        identifier = _promote_generation_identifier(
            normalized_source,
            fallback_prefix=fallback_prefix,
            max_length=max_length,
        )
        previous_source = sources_by_identifier.get(identifier)
        if previous_source is not None and previous_source != normalized_source:
            raise ValueError(
                f"{field_name} normalization collision: "
                f"{previous_source!r} and {normalized_source!r} -> {identifier!r}"
            )
        if previous_source is not None and not allow_repeated_source:
            promoted.append(identifier)
            continue
        sources_by_identifier[identifier] = normalized_source
        promoted.append(identifier)
    return promoted


def _promote_generation_identifier(
    value: str,
    *,
    fallback_prefix: str,
    max_length: int,
) -> str:
    if not value:
        raise ValueError("generation identifier cannot be empty")
    if re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.-]*$", value) and len(value) <= max_length:
        return value

    ascii_tokens = re.findall(r"[A-Za-z0-9]+", value)
    base = ".".join(token.casefold() for token in ascii_tokens)
    if not base or not base[0].isalpha():
        base = f"{fallback_prefix}.{base}" if base else fallback_prefix

    needs_digest = not value.isascii() or len(base) > max_length
    suffix = ""
    if needs_digest:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        suffix = f".u{digest}"
    available = max_length - len(suffix)
    base = base[:available].rstrip("._-") or fallback_prefix
    return f"{base}{suffix}"


VerificationLayerStatus = Literal["passed", "failed", "blocked", "not_assessed"]


class VerificationLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationLayerStatus
    reasons: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class ModelAdequacyLayer(VerificationLayer):
    candidate_roles: list[Literal["baseline", "recommended", "alternative"]] = Field(
        default_factory=list
    )
    checks: dict[str, VerificationLayerStatus] = Field(default_factory=dict)

    @model_validator(mode="after")
    def prevent_unsupported_pass(self) -> ModelAdequacyLayer:
        if self.status != "passed":
            return self
        roles = set(self.candidate_roles)
        if "baseline" not in roles:
            raise ValueError("model_adequate requires a baseline candidate")
        if "recommended" not in roles:
            raise ValueError("model_adequate requires a recommended candidate")
        failed_checks = [
            name
            for name, status in self.checks.items()
            if status in {"failed", "blocked", "not_assessed"}
        ]
        if failed_checks:
            raise ValueError(
                "model_adequate cannot pass while checks fail: "
                + ", ".join(sorted(failed_checks))
            )
        return self


class CounterevidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subproblem_id: str = Field(min_length=1)
    challenged_candidate_id: str = Field(min_length=1)
    status: Literal["excluded", "not_excluded", "not_assessed"]
    exclusion_reason: str = Field(min_length=1)
    check_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    applicability_boundary: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_evidence_for_exclusion(self) -> CounterevidenceRecord:
        if self.status != "excluded":
            return self
        if not self.check_refs:
            raise ValueError("excluded route requires check_refs")
        if not self.evidence_refs:
            raise ValueError("excluded route requires evidence_refs")
        return self


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    model_id: str
    status: Literal["verified", "partial", "blocked"] = "partial"
    coverage_status: Literal["complete", "partial", "blocked"] = "complete"
    execution_verified: VerificationLayer
    evidence_supported: VerificationLayer
    model_adequate: ModelAdequacyLayer
    counterevidence_records: list[CounterevidenceRecord] = Field(default_factory=list)
    input_checks: list[dict[str, Any]] = Field(default_factory=list)
    equation_checks: list[dict[str, Any]] = Field(default_factory=list)
    unit_checks: list[dict[str, Any]] = Field(default_factory=list)
    constraint_checks: list[dict[str, Any]] = Field(default_factory=list)
    residual_checks: list[dict[str, Any]] = Field(default_factory=list)
    sensitivity_checks: list[dict[str, Any]] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    artifact_refs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def aggregate_layer_statuses(self) -> VerificationReport:
        if self.model_adequate.status == "passed" and not any(
            record.status == "excluded" for record in self.counterevidence_records
        ):
            raise ValueError(
                "model_adequate requires counterevidence for at least one excluded route"
            )
        if self.execution_verified.status != "passed" or self.coverage_status == "blocked":
            self.status = "blocked"
        elif (
            self.coverage_status == "complete"
            and
            self.evidence_supported.status == "passed"
            and self.model_adequate.status == "passed"
        ):
            self.status = "verified"
        else:
            self.status = "partial"
        return self
