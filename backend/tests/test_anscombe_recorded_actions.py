"""Replay only the submitted mathematical actions from the frozen eight-call R10 run."""
import asyncio
import json
from pathlib import Path

from app.guidance import workflow
from math_tool_fixtures import scripted_model_reply, tool_reply
from scripts.run_closeout import prepare_run, save
from scripts.verify_anscombe_fit import audit_run


FIXTURE = Path(__file__).parent / "fixtures/anscombe_linear_fit"


def test_r10_recorded_actions_publish_the_original_reviewed_model(tmp_path, monkeypatch):
    run = tmp_path / "run-01"
    task = prepare_run(FIXTURE / "case.json", run)
    turns = iter(json.loads((FIXTURE / "r10_actions.json").read_bytes()))

    class RecordedMath:
        def __init__(self, **settings):
            self.__dict__.update(settings)

        async def chat(self, *, agent_name, **kwargs):
            turn = next(turns)
            assert turn["agent"] == agent_name
            if "actions" in turn:
                assert kwargs.get("tools")
                return tool_reply(*turn["actions"])
            assert not kwargs.get("tools")
            return scripted_model_reply(turn["submission"], kwargs)

    monkeypatch.setattr(workflow, "LLM", RecordedMath)

    async def replay():
        pipeline = workflow.build_default_guidance_pipeline("standard")
        return [event async for event in pipeline.run(run.name, task)]

    events = asyncio.run(replay())
    assert next(turns, None) is None, "some recorded responses were not consumed"
    approved = json.loads((run / "approved_model_ir.json").read_bytes())
    assert approved["review_status"] == "approved"
    binding = approved["review_binding"]
    # These canonical digests were recorded before the compiler repair.
    assert binding["approved_model_sha256"] == "0b0e4f63f35ca55407ba1a4b57384810a3c30c3e4f1a36ef559a434bb888943b"
    assert binding["review_sha256"] == "e9ae88028450429b6e1d7973e8390d38795525a4ef18e915da5812f5f1ff7b09"
    assert binding["input_catalog_sha256"] == "52661cea17aed54de93929a0f63e5e103700cad292cdbc8934e8cae3d872f0a4"

    terminal = events[-1]
    assert terminal["type"] == "result" and terminal["data"]["status"] == "completed"
    save(run / "pipeline_events.json", events)
    save(run / "run_summary.json", {"status": terminal["data"]["status"], "pipeline_result": terminal["data"]})
    audit = audit_run(run)
    assert audit["passed"], audit
