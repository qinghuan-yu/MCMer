"""运行冻结的封档案例；复用默认生产流程，只负责预算、输入冻结和实测记录。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
MODEL = "deepseek/deepseek-flash"
ENDPOINT = "https://api.deepseek.com"
DEFAULT_CASE = BACKEND / "closeout/portfolio_case.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value, secret=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    if secret:
        text = text.replace(secret, "[REDACTED]")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def validate_case(case_path: Path):
    case_path = Path(case_path).resolve()
    case = json.loads(case_path.read_text(encoding="utf-8"))
    task_path = (case_path.parent / case["task_file"]).resolve()
    if not task_path.is_relative_to(case_path.parent) or sha(task_path) != case["task_sha256"]:
        raise ValueError("frozen task SHA mismatch")
    budget = case["budget"]
    if budget["model"] != "deepseek-flash" or budget["endpoint"] != ENDPOINT:
        raise ValueError("model or endpoint is outside authorization")
    for key, maximum in [("max_calls", 24), ("max_total_tokens", 400000),
                         ("max_total_run_seconds", 1800), ("max_runs", 2)]:
        if type(budget[key]) is not int or not 0 < budget[key] <= maximum:
            raise ValueError("frozen budget exceeds closeout boundary")
    return case


class BudgetLedger:
    def __init__(self, path, limits, case_sha):
        self.path = Path(path)
        binding = {"limits": limits, "case_sha256": case_sha}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state["binding"] != binding:
                raise ValueError("ledger binding changed; budgets cannot be reset")
        else:
            self.state = {"binding": binding, "calls": [], "runs": []}
            self.flush()
        self.limits = limits

    def flush(self):
        save(self.path, self.state)

    def charged_tokens(self):
        return sum(c["usage"]["total_tokens"] if c.get("usage") else c["reserved_tokens"]
                   for c in self.state["calls"])

    def reserve(self, *, history, max_tokens, agent):
        if len(self.state["calls"]) >= self.limits["max_calls"]:
            raise RuntimeError("total API call budget exhausted")
        if type(max_tokens) is not int or not 0 < max_tokens <= 32768:
            raise ValueError("invalid output token limit")
        # UTF-8 字节数作为文本 token 的保守预留，并包含消息封装余量；不调用网络计数器。
        reserved = len(json.dumps(history, ensure_ascii=False).encode("utf-8")) + max_tokens + 1024
        if self.charged_tokens() + reserved > self.limits["max_total_tokens"]:
            raise RuntimeError("total token budget cannot reserve this request")
        record = {"number": len(self.state["calls"]) + 1, "agent": agent, "status": "started",
                  "reserved_tokens": reserved, "max_tokens": max_tokens, "usage": None}
        self.state["calls"].append(record)
        self.flush()
        return record["number"]

    def finish(self, number, *, status, usage=None, **details):
        if usage is not None and (type(usage.get("total_tokens")) is not int or usage["total_tokens"] <= 0):
            usage = None
        self.state["calls"][number - 1].update(status=status, usage=usage, **details)
        self.flush()

    def remaining_seconds(self):
        return self.limits["max_total_run_seconds"] - sum(r.get("seconds", 0) for r in self.state["runs"])

    def begin_run(self, *, code_sha, repair_evidence):
        runs = self.state["runs"]
        if any(r["status"] == "started" for r in runs):
            raise RuntimeError("unfinished run must be reconciled before another attempt")
        if len(runs) >= self.limits["max_runs"] or self.remaining_seconds() <= 0:
            raise RuntimeError("total run budget exhausted")
        if runs and (not repair_evidence or runs[-1]["code_sha"] == code_sha):
            raise RuntimeError("second run requires changed code and repair evidence")
        runs.append({"number": len(runs) + 1, "code_sha": code_sha,
                     "repair_evidence": repair_evidence, "status": "started"})
        self.flush()
        return len(runs)

    def end_run(self, *, status, seconds):
        self.state["runs"][-1].update(status=status, seconds=seconds)
        self.flush()


def prepare_run(case_path, root):
    case = validate_case(case_path)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    target = root / "inputs/question" / case["task_file"]
    target.parent.mkdir(parents=True)
    shutil.copy2(Path(case_path).parent / case["task_file"], target)
    from app.services.source_ingest import ingest_question_source
    from app.artifacts.evidence_profile import build_evidence_profile
    task = {"task_id": root.name, "work_dir": str(root), "workflow_mode": "standard", "task_type": "guidance"}
    task.update(ingest_question_source(str(target), task))
    build_evidence_profile(root)
    save(root / "task_input.json", task)
    return task


def build_pipeline(*, ledger=None, root=None, secret="", live=False):
    from app.guidance import workflow
    from app.core.llm.llm import LLM

    class RecordedLLM(LLM):
        def __init__(self, **kwargs):
            kwargs.pop("model")
            super().__init__(model=MODEL, api_key=secret, base_url=ENDPOINT, **kwargs)
            if self.model != MODEL or self.base_url != ENDPOINT or self.api_key != secret:
                raise ValueError("provider or credential binding changed")

        async def chat(self, **kwargs):
            if kwargs.get("max_attempts") != 1 or kwargs.get("tools"):
                raise ValueError("unrecorded retries or generated-code tools are prohibited")
            outgoing = {"model": self.model, "endpoint": self.base_url,
                        "temperature": self.temperature, **kwargs}
            if secret in json.dumps(outgoing, ensure_ascii=False):
                raise ValueError("credential appeared in request content")
            number = ledger.reserve(history=kwargs["history"], max_tokens=kwargs["max_tokens"], agent=kwargs.get("agent_name"))
            save(root / "api" / f"{number:03d}_request.json", outgoing, secret)
            print(json.dumps({"event": "api_started", "number": number, "agent": kwargs.get("agent_name")}), flush=True)
            started = time.monotonic()
            try:
                response = await super().chat(**kwargs)
                save(root / "api" / f"{number:03d}_response.json", response.model_dump(mode="json"), secret)
                usage = response.usage.model_dump() if response.usage else None
                ledger.finish(number, status="returned", usage=usage,
                              finish_reason=response.choices[0].finish_reason)
                if response.model != "deepseek-flash":
                    raise RuntimeError("provider returned an unexpected model")
                return response
            except BaseException as error:
                message = str(error).replace(secret, "[REDACTED]")
                recorded = ledger.state["calls"][number - 1]
                ledger.finish(number, status="failed", usage=recorded.get("usage"),
                              error_type=type(error).__name__, error=message)
                save(root / "api" / f"{number:03d}_failure.json", ledger.state["calls"][number - 1], secret)
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise RuntimeError(message) from None
            finally:
                ledger.state["calls"][number - 1]["seconds"] = round(time.monotonic() - started, 3)
                ledger.flush()
                print(json.dumps({"event": "api_finished", **ledger.state["calls"][number - 1]}, ensure_ascii=True), flush=True)

    class NoNetworkLLM:
        def __init__(self, **kwargs):
            self.model, self.base_url = MODEL, ENDPOINT

        async def chat(self, **kwargs):
            raise AssertionError("dry preparation must not call the model")

    original = workflow.LLM
    workflow.LLM = RecordedLLM if live else NoNetworkLLM
    try:
        return workflow.build_default_guidance_pipeline("standard")
    finally:
        workflow.LLM = original


def code_sha():
    files = [*sorted((BACKEND / "app").rglob("*.py")), Path(__file__).resolve()]
    payload = [(str(p.relative_to(BACKEND)), sha(p)) for p in files]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repair-evidence", type=Path)
    args = parser.parse_args()
    case = validate_case(args.case)
    suite_root = args.root.resolve()
    suite_root.mkdir(parents=True, exist_ok=True)
    # 拒绝并发写入同一账本。中断留下锁时须核对在途请求，不能另起目录清零。
    lock = suite_root / "run.lock"
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(lock_fd)
    try:
        if not args.live:
            root = suite_root / "dry"
            prepare_run(args.case, root)
            pipeline = build_pipeline(live=False)
            metadata = {"mode": "dry", "api_calls": 0, "case_sha256": sha(args.case),
                        "stages": {k: type(v).__name__ for k, v in pipeline._stages.items()}}
            save(root / "run_summary.json", metadata)
            print(json.dumps(metadata))
            return
        from app.services.config_manager import config_manager
        from app.utils.log_util import logger
        secret = config_manager.get_effective_key("DEEPSEEK_API_KEY")
        if not secret:
            raise RuntimeError("DeepSeek credential is unavailable")
        logger.remove()
        logger.add(lambda message: print(str(message).replace(secret, "[REDACTED]"), end="", flush=True), level="WARNING")
        ledger = BudgetLedger(suite_root / "ledger.json", case["budget"], sha(args.case))
        repair = sha(args.repair_evidence) if args.repair_evidence else None
        config_hash = sha(config_manager.CONFIG_FILE)
        current_code = code_sha()
        number = ledger.begin_run(code_sha=current_code, repair_evidence=repair)
        root = suite_root / f"run-{number:02d}"
        started = time.monotonic()
        summary = {"status": "failed", "run": number}
        events = []
        try:
            task = prepare_run(args.case, root)
            pipeline = build_pipeline(ledger=ledger, root=root, secret=secret, live=True)
            metadata = {"mode": "live", "model": MODEL, "endpoint": ENDPOINT,
                "case_sha256": sha(args.case), "code_sha256": current_code, "budget": case["budget"],
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip(),
                "stages": {k: type(v).__name__ for k, v in pipeline._stages.items()}}
            save(root / "run_metadata.json", metadata, secret)
            async with asyncio.timeout(ledger.remaining_seconds()):
                async for event in pipeline.run(root.name, task):
                    events.append(event)
                    save(root / "pipeline_events.json", events, secret)
                    stage = event.get("data", {}).get("stage", event["type"])
                    snapshot = root / "stage_snapshots" / f"{len(events):02d}-{stage}"
                    snapshot.mkdir(parents=True)
                    for path in root.glob("*.json"):
                        shutil.copy2(path, snapshot / path.name)
                    if event["type"] == "result":
                        summary.update(status=event["data"].get("status", "failed"), pipeline_result=event["data"])
                    print(json.dumps({"event": "pipeline", "stage": stage}, ensure_ascii=True), flush=True)
        except Exception as error:
            summary.update(error_type=type(error).__name__, error=str(error).replace(secret, "[REDACTED]"))
        finally:
            summary["seconds"] = round(time.monotonic() - started, 3)
            summary["persistent_config_unchanged"] = config_hash == sha(config_manager.CONFIG_FILE)
            summary["code_unchanged"] = current_code == code_sha()
            summary["task_unchanged"] = sha(root / "inputs/question" / case["task_file"]) == case["task_sha256"]
            summary["credential_scan_passed"] = all(secret.encode() not in p.read_bytes() for p in root.rglob("*") if p.is_file())
            ledger.end_run(status=summary["status"], seconds=summary["seconds"])
            summary["calls_total"] = len(ledger.state["calls"])
            summary["known_tokens"] = sum(c["usage"]["total_tokens"] for c in ledger.state["calls"] if c.get("usage"))
            summary["unknown_usage_calls"] = sum(not c.get("usage") for c in ledger.state["calls"])
            summary["charged_tokens"] = ledger.charged_tokens()
            save(root / "run_summary.json", summary, secret)
            print(json.dumps({"event": "finished", **summary}, ensure_ascii=True), flush=True)
    finally:
        lock.unlink()


if __name__ == "__main__":
    asyncio.run(main())
