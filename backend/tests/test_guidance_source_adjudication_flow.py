"""来源完整许可的前置应用、重载和数学修订绑定。"""

import asyncio
import json

import pytest

from app.guidance.pipeline import _preserve_revision_request
from app.guidance.recovery import canonical_digest, load_source_preflight
from app.guidance.stages import SourceStage
from test_guidance_source_revision_flow import _case, _write


def test_source_stage_applies_once_before_models_and_preserves_original_bytes(tmp_path):
    original, resolved, _, record, _ = _case(tmp_path)
    original_bytes = (json.dumps(original.model_dump(mode="json"), indent=4) + "\n").encode("utf-8")
    path = tmp_path / "problem_spec.json"
    path.write_bytes(original_bytes)
    calls = []

    class Source:
        async def prepare_sources(self, **kwargs):
            calls.append(kwargs)
            assert not (tmp_path / "model_ir.json").exists()
            return resolved, record

    stage = SourceStage(source_adjudicator=Source())
    asyncio.run(stage.run(task_id="source-first", task={}, input_path=path, output_path=path))
    assert (tmp_path / "problem_spec_original.json").read_bytes() == original_bytes
    assert load_source_preflight(tmp_path, resolved) == record
    assert len(calls) == 1
    with pytest.raises(ValueError, match="once"):
        asyncio.run(stage.run(task_id="source-twice", task={}, input_path=path, output_path=path))
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["missing", "old_record", "permission", "original_problem", "source_file", "current_problem"])
def test_recovery_refuses_missing_legacy_or_changed_complete_permission(tmp_path, change):
    _, spec, _, _, record, _ = _write(tmp_path)
    assert load_source_preflight(tmp_path, spec) == record
    path = tmp_path / "source_preflight.json"
    if change == "missing":
        path.unlink()
    elif change == "old_record":
        path.write_text('{"proposals": [], "checks": [], "assessment_status": "reviewed"}', encoding="utf-8")
    elif change == "source_file":
        (tmp_path / "question.txt").write_text("A different original statement.", encoding="utf-8")
    elif change == "current_problem":
        spec = spec.model_copy(update={"task_summary": "A substituted current problem."})
    elif change == "original_problem":
        original = json.loads((tmp_path / "problem_spec_original.json").read_text(encoding="utf-8"))
        original["task_summary"] = "A substituted original problem."
        (tmp_path / "problem_spec_original.json").write_text(json.dumps(original), encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["adjudication"]["checks"][0]["status"] = "contradicted"
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_source_preflight(tmp_path, spec)


@pytest.mark.parametrize("change", ["missing_binding", "permission", "review", "model", "projection"])
def test_mathematical_revision_requires_unchanged_approval_and_source_binding(tmp_path, change):
    _, _, _, _, _, original = _write(tmp_path)
    filename = {"missing_binding": "approved_model_ir.json", "permission": "source_preflight.json",
                "review": "model_review.json", "model": "model_ir.json", "projection": "approved_model_ir.json"}[change]
    path = tmp_path / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    if change == "missing_binding":
        payload["review_binding"] = None
    elif change == "permission":
        payload["adjudication"]["checks"][0]["rationale"] = "A replaced independent source interpretation."
        payload["binding_sha256"] = canonical_digest({key: value for key, value in payload.items() if key != "binding_sha256"})
    elif change == "review":
        payload["subproblem_reviews"][1]["required_revisions"][0]["description"] = "A different model repair."
    elif change == "model":
        payload["subproblems"][0]["objective"] = "A changed mathematical objective."
    else:
        payload["approved_model_ir"]["subproblems"][1]["blocking_reason"] = "A changed execution projection."
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="binding|permission|source"):
        _preserve_revision_request(tmp_path, tmp_path / "model_ir.json")
    assert (tmp_path / "problem_spec.json").read_bytes() == original
    assert not (tmp_path / "revision_request.json").exists()


def test_source_stage_rejects_input_changes_during_async_interpretation(tmp_path):
    original, resolved, _, record, _ = _case(tmp_path)
    path = tmp_path / "problem_spec.json"
    path.write_text(original.model_dump_json(), encoding="utf-8")

    class Source:
        async def prepare_sources(self, **kwargs):
            path.write_text(original.model_copy(update={"task_summary": "Changed while waiting"}).model_dump_json(), encoding="utf-8")
            return resolved, record

    with pytest.raises(ValueError, match="input changed"):
        asyncio.run(SourceStage(source_adjudicator=Source()).run(
            task_id="changed-source", task={}, input_path=path, output_path=path,
        ))
    assert not (tmp_path / "source_preflight.json").exists()
