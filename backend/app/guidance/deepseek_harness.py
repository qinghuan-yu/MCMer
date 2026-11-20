"""通过标准输入输出来驱动官方 DeepSeek Harness 的受限数学会话。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


RUNTIME_DIR = Path(__file__).resolve().parents[2] / "dsh"
_MAX_PACKET_BYTES = 8 * 1024 * 1024


async def run_math_turn(*, prompt: str, system_prompt: str, tools: list[dict],
                        request_model: Callable[[list[dict], list[dict]], Awaitable[dict]],
                        execute_tool: Callable[[str, dict], dict], is_done: Callable[[], bool],
                        max_steps: int, timeout_seconds: float) -> None:
    """官方循环只可通过父进程请求模型、执行已登记工具。只有提交成功才可完成。"""
    if type(max_steps) is not int or max_steps < 1 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("invalid DeepSeek Harness step or time budget")
    node = shutil.which("node")
    if not node or not (RUNTIME_DIR / "node_modules/@deepseek-ai/dsh-agent-loop/package.json").is_file():
        raise RuntimeError(f"DeepSeek Harness requires Node 22.12+ and npm ci --ignore-scripts in {RUNTIME_DIR}")
    allowed = {tool["function"]["name"] for tool in tools}
    if len(allowed) != len(tools):
        raise ValueError("duplicate math tool names")

    async def turn():
        process = await asyncio.create_subprocess_exec(
            node, str(RUNTIME_DIR / "math-turn.mjs"), cwd=RUNTIME_DIR,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, limit=_MAX_PACKET_BYTES,
        )

        async def send(value):
            line = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
            if len(line) > _MAX_PACKET_BYTES:
                raise ValueError("DeepSeek Harness packet exceeds the size limit")
            process.stdin.write(line)
            await process.stdin.drain()

        steps = 0
        try:
            await send({"prompt": prompt, "system_prompt": system_prompt, "tools": tools})
            while line := await process.stdout.readline():
                event = json.loads(line)
                if event["op"] == "model":
                    if is_done():
                        await send({"role": "assistant", "content": "submitted"})
                        continue
                    if steps >= max_steps:
                        raise RuntimeError("DeepSeek Harness exhausted its modeling step budget")
                    steps += 1
                    await send(await request_model(event["history"], event["tools"]))
                elif event["op"] == "tool":
                    if event["name"] not in allowed:
                        raise RuntimeError("DeepSeek Harness requested an unregistered tool")
                    # 同一批工具可能在提交后还有编辑操作，提交后草稿保持封存。
                    await send({"error": "model already submitted"} if is_done()
                               else execute_tool(event["name"], event["arguments"]))
                elif event["op"] == "done":
                    if not is_done():
                        raise RuntimeError("DeepSeek Harness stopped before a validated model was submitted")
                    process.stdin.close()
                    if await process.wait() != 0:
                        raise RuntimeError("DeepSeek Harness did not shut down cleanly")
                    return
                elif event["op"] == "error":
                    raise RuntimeError(f"DeepSeek Harness: {event['message']}")
                else:
                    raise RuntimeError("unrecognized DeepSeek Harness event")
            raise RuntimeError(f"DeepSeek Harness exited early; check Node and npm ci --ignore-scripts in {RUNTIME_DIR}")
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    await asyncio.wait_for(turn(), timeout=timeout_seconds)
