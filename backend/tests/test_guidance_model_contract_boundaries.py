from importlib import import_module

import pytest


@pytest.mark.parametrize(("module", "name"), [
    ("app.guidance.model_ir_drafts", "ModelIRGenerationDraft"),
    ("app.guidance.model_ir_drafts", "IndexedLinearCoreDraft"),
    ("app.guidance.model_review_contracts", "ModelIRReview"),
    ("app.guidance.model_review_contracts", "ApprovedModelIR"),
])
def test_generation_and_review_contracts_have_distinct_owners(module, name):
    contract = getattr(import_module(module), name)
    assert contract.__module__ == module
    assert contract.model_json_schema()["type"] == "object"


def test_canonical_model_schema_does_not_depend_on_draft_types():
    from app.guidance.model_ir import ModelIR

    assert not any("Draft" in name for name in ModelIR.model_json_schema()["$defs"])
