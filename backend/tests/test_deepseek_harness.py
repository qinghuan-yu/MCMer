import asyncio
import json
from pathlib import Path, PurePosixPath

import pytest
import yaml


def test_compose_keeps_the_image_runtime_visible_alongside_development_mounts():
    repo = Path(__file__).resolve().parents[2]
    backend = yaml.safe_load((repo / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["backend"]
    mounts = {}
    for volume in backend["volumes"]:
        source, target, *_ = volume.split(":")
        mounts[PurePosixPath(target)] = source

    runtime = PurePosixPath("/app/dsh/node_modules")
    assert all(not runtime.is_relative_to(target) and not target.is_relative_to(runtime) for target in mounts)
    assert mounts[PurePosixPath("/app/app")] == "./backend/app"
    assert mounts[PurePosixPath(backend["environment"]["WORK_DIR"])] == "./work_dir"
    assert mounts[PurePosixPath("/app/.local")] == "./backend/.local"
    assert mounts[PurePosixPath("/app/logs")] == "./backend/logs"
    assert mounts[PurePosixPath("/app/project")] == "./backend/project"


def test_official_runtime_routes_only_math_tools_and_preserves_observations():
    from app.guidance.deepseek_harness import run_math_turn

    transcripts = []
    executed = []
    tool = {"type": "function", "function": {
        "name": "edit_model", "description": "Edit the mathematical model",
        "parameters": {"type": "object", "properties": {"part": {"type": "string"}},
                       "required": ["part"]},
    }}

    async def model(history, tools):
        assert [item["function"]["name"] for item in tools] == ["edit_model"]
        transcripts.append(history)
        idx = len(transcripts)
        return {"role": "assistant", "content": None, "reasoning_content": f"reason{idx}",
                "tool_calls": [{"id": f"call-{idx}", "type": "function",
                                "function": {"name": "edit_model", "arguments": json.dumps({"part": str(idx)})}}]}

    def execute(name, arguments):
        executed.append((name, arguments["part"]))
        return {"accepted": len(executed) == 2}

    asyncio.run(run_math_turn(
        prompt="Model this problem", system_prompt="Use mathematical evidence.",
        tools=[tool], request_model=model, execute_tool=execute,
        is_done=lambda: len(executed) == 2, max_steps=2, timeout_seconds=30,
    ))
    assert executed == [("edit_model", "1"), ("edit_model", "2")]
    assert len(transcripts) == 2
    assert json.loads(transcripts[1][-1]["content"]) == {"accepted": False}
    assert transcripts[1][-1]["tool_call_id"] == "call-1"
    assert transcripts[1][-2]["reasoning_content"] == "reason1"


def test_official_runtime_blocks_text_only_completion():
    from app.guidance.deepseek_harness import run_math_turn

    async def model(history, tools):
        return {"role": "assistant", "content": "done"}

    with pytest.raises(RuntimeError, match="submitted"):
        asyncio.run(run_math_turn(prompt="test", system_prompt="test", tools=[],
                                  request_model=model, execute_tool=None, is_done=lambda: False,
                                  max_steps=1, timeout_seconds=30))


def test_runtime_orders_a_batch_and_propagates_budget_exhaustion():
    from app.guidance.deepseek_harness import run_math_turn

    seen = []
    calls = []
    tool = {"type": "function", "function": {"name": "edit", "description": "Edit",
                                            "parameters": {"type": "object"}}}

    async def model(history, tools):
        calls.append(1)
        return {"tool_calls": [
            {"id": f"c-{n}", "function": {"name": "edit", "arguments": json.dumps({"n": n})}}
            for n in (1, 2, 3)
        ]}

    def execute(name, arguments):
        seen.append(arguments["n"])
        if len(seen) == 2:
            raise RuntimeError("host budget exhausted")
        return {"accepted": True}

    with pytest.raises(RuntimeError, match="host budget exhausted"):
        asyncio.run(run_math_turn(prompt="batch", system_prompt="test", tools=[tool],
                                  request_model=model, execute_tool=execute, is_done=lambda: False,
                                  max_steps=2, timeout_seconds=30))
    assert calls == [1] and seen == [1, 2]
