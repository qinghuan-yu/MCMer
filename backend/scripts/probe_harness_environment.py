"""以虚构数据验证 H1 的本地隔离与状态持久性，不调用模型或外部服务。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid


def _command(arguments, *, timeout=30, input_text=None):
    return subprocess.run(
        arguments, input=input_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _state_probe(root: Path):
    database = root / "state-probe.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE actions (id TEXT PRIMARY KEY, state TEXT, reserved INTEGER)")
        connection.execute("INSERT INTO actions VALUES ('call-1', 'started', 1)")
    # 子进程在未提交结果前退出；已提交的开始记录和预算必须保留。
    child = _command([
        sys.executable, "-c",
        "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute(\"UPDATE actions SET state='finished'\"); os._exit(9)",
        str(database),
    ])
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT state,reserved FROM actions").fetchone()
    return {
        "child_exit": child.returncode, "state_after_crash": row[0],
        "reserved_after_crash": row[1], "passed": child.returncode == 9 and row == ("started", 1),
        "scope": "SQLite durability only; not a production harness recovery test",
    }


BOUNDARY_CODE = r"""
import errno, importlib.metadata, json, os, pathlib, socket
result = {}
result['input_readable'] = pathlib.Path('/inputs/source.txt').read_text() == 'public-dummy-input'
try:
    pathlib.Path('/inputs/source.txt').write_text('mutated')
    result['input_read_only'] = False
except OSError:
    result['input_read_only'] = True
try:
    pathlib.Path('/root-write-probe').write_text('mutated')
    result['root_read_only'] = False
except OSError:
    result['root_read_only'] = True
result['host_secret_not_inherited'] = 'MCMER_H1_FAKE_SECRET' not in os.environ
result['host_sibling_not_mounted'] = not pathlib.Path('/inputs/../outside.txt').exists()
pathlib.Path('/work/link').symlink_to('/inputs/../outside.txt')
result['symlink_cannot_reach_host_sibling'] = not pathlib.Path('/work/link').exists()
result['non_root'] = os.getuid() == 65534
status = pathlib.Path('/proc/self/status').read_text()
result['no_new_privileges'] = 'NoNewPrivs:\t1' in status
result['capabilities_dropped'] = 'CapEff:\t0000000000000000' in status
result['seccomp_enabled'] = 'Seccomp:\t2' in status
result['cgroups'] = {name: pathlib.Path('/sys/fs/cgroup/' + name).read_text().strip()
                     for name in ['memory.max', 'pids.max', 'cpu.max']}
try:
    with socket.create_connection(('198.51.100.1', 443), timeout=1):
        result['network_disabled'] = False
except OSError:
    result['network_disabled'] = True
try:
    with pathlib.Path('/work/too-large').open('wb') as stream:
        for _ in range(64):
            stream.write(b'x' * 1024 * 1024)
    result['workspace_quota_enforced'] = False
except OSError as error:
    result['workspace_quota_enforced'] = error.errno == errno.ENOSPC
finally:
    pathlib.Path('/work/too-large').unlink(missing_ok=True)
result['dependencies'] = {}
for name in ['numpy', 'pandas', 'scipy', 'openpyxl']:
    try:
        result['dependencies'][name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        result['dependencies'][name] = None
import numpy as np, pandas as pd
from scipy.optimize import linprog
frame = pd.DataFrame({'x': [1., 2., 3.], 'y': [2., 4., 6.]})
coefficient = np.linalg.lstsq(frame[['x']].to_numpy(), frame['y'].to_numpy(), rcond=None)[0][0]
solution = linprog([1.], bounds=[(2., 10.)], method='highs')
result['numerical_dependencies_execute'] = bool(abs(coefficient - 2.) < 1e-10 and solution.success and abs(solution.fun - 2.) < 1e-10)
print(json.dumps(result))
"""


def run(output: Path, image: str):
    allowed = (Path(__file__).resolve().parents[1] / ".local").resolve()
    output = output.resolve()
    if not output.is_relative_to(allowed):
        raise ValueError("probe output must stay inside backend/.local")
    if output.exists():
        raise FileExistsError(output)
    root = output.with_suffix("")
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "source.txt").write_text("public-dummy-input", encoding="utf-8")
    (root / "outside.txt").write_text("unmounted-dummy-sibling", encoding="utf-8")
    empty = root / "empty"
    empty.mkdir()
    report = {"kind": "H1 environment spike, no model calls", "state_probe": _state_probe(root)}
    inspection = _command(["docker", "image", "inspect", image])
    if inspection.returncode:
        raise RuntimeError("local probe image is unavailable; no automatic pull")
    metadata = json.loads(inspection.stdout)[0]
    image_id = metadata["Id"]
    report["image_id"] = image_id
    # 只记录镜像身份；镜像环境值不写入日志或报告。
    env_args = []
    for entry in metadata["Config"].get("Env", []):
        name = entry.split("=", 1)[0]
        if name not in {"PATH", "LANG", "LC_ALL", "PYTHON_VERSION", "PYTHON_PIP_VERSION"}:
            env_args.extend(["--env", name + "="])
    os.environ["MCMER_H1_FAKE_SECRET"] = "not-a-real-secret"
    created = []
    try:
        def invoke(code, *, label, memory="256m", timeout=20):
            name = "mcmer-h1-" + label + "-" + uuid.uuid4().hex[:10]
            created.append(name)
            arguments = [
                "docker", "run", "--name", name, "--pull=never",
                "--network=none", "--read-only", "--user=65534:65534",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--memory=" + memory, "--memory-swap=" + memory,
                "--cpus=1", "--pids-limit=32",
                "--tmpfs=/work:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
                "--mount", "type=bind,source=" + str(inputs) + ",target=/inputs,readonly",
                "--mount", "type=bind,source=" + str(empty) + ",target=/app,readonly",
                "--workdir=/work", *env_args,
                "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1",
                "--env=MKL_NUM_THREADS=1", "--entrypoint=python", "-i", image_id,
                "-c", code,
            ]
            try:
                result = _command(arguments, timeout=timeout)
            except subprocess.TimeoutExpired:
                result = None
            state_raw = _command(["docker", "inspect", "--format={{json .State}}", name])
            state = json.loads(state_raw.stdout) if state_raw.returncode == 0 else {}
            removed = _command(["docker", "rm", "-f", name])
            if removed.returncode != 0:
                raise RuntimeError("probe container cleanup failed: " + name)
            absent = _command(["docker", "inspect", name]).returncode != 0
            return result, state, absent

        result, _, removed = invoke(BOUNDARY_CODE, label="boundaries")
        if result is None or result.returncode:
            raise RuntimeError("boundary probe failed: " + (result.stderr[-1200:] if result else "timeout"))
        report["boundaries"] = json.loads(result.stdout)
        report["boundary_container_removed"] = removed
        result, state, removed = invoke(
            "buffer=bytearray(512*1024*1024)", label="memory", memory="128m",
        )
        report["memory_limit"] = {
            "oom_killed": state.get("OOMKilled", False),
            "exit_code": result.returncode if result else None, "container_removed": removed,
        }
        result, state, removed = invoke(
            "import subprocess,time; subprocess.Popen(['python','-c','import time; time.sleep(60)']); "
            "print('child-started',flush=True); time.sleep(60)",
            label="cancel", timeout=4,
        )
        report["cancel"] = {
            "timeout_observed": result is None, "was_running": state.get("Running", False),
            "container_removed_with_children": removed,
        }
        bounds = report["boundaries"]
        checks = [value for value in bounds.values() if isinstance(value, bool)]
        report["passed"] = (
            all(checks) and report["state_probe"]["passed"]
            and bounds["cgroups"]["memory.max"] == str(256 * 1024 * 1024)
            and bounds["cgroups"]["pids.max"] == "32"
            and bounds["cgroups"]["cpu.max"] == "100000 100000"
            and all(bounds["dependencies"].values())
            and report["memory_limit"]["oom_killed"] and report["memory_limit"]["container_removed"]
            and report["cancel"]["timeout_observed"]
            and report["cancel"]["was_running"]
            and report["cancel"]["container_removed_with_children"] and report["boundary_container_removed"]
        )
    finally:
        for name in created:
            if _command(["docker", "inspect", name]).returncode == 0:
                _command(["docker", "rm", "-f", name])
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"report": str(output), "passed": report.get("passed", False)}))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", default="mcmer-backend:latest")
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.image))
