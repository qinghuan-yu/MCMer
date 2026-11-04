"""测试只能依赖入库 fixture，新 clone 后全量回归必须可复现。"""

import hashlib
import json
from pathlib import Path

TESTS_ROOT = Path(__file__).parent
REPOSITORY_ROOT = TESTS_ROOT.parents[1]
FIXTURES_ROOT = TESTS_ROOT / "fixtures"


def test_tests_do_not_read_untracked_run_work_dirs() -> None:
    offenders = sorted(
        path.relative_to(TESTS_ROOT).as_posix()
        for path in [*TESTS_ROOT.glob("*.py"), *FIXTURES_ROOT.rglob("*.json")]
        if path.name != Path(__file__).name
        and "project/work_dir" in path.read_text(encoding="utf-8")
    )
    assert offenders == [], "tests must use tracked fixtures: " + ", ".join(offenders)


def test_real_problem_baseline_attachments_are_tracked_with_recorded_hashes() -> None:
    baseline = json.loads(
        (FIXTURES_ROOT / "guidance_migration_baseline.json").read_text(encoding="utf-8")
    )
    for case in baseline["cases"]:
        source = (REPOSITORY_ROOT / case["source_work_dir"]).resolve()
        assert source.is_relative_to(FIXTURES_ROOT.resolve()), case["id"]
        attachments = sorted((source / "data").glob("*.xlsx"))
        assert len(attachments) == 1, case["id"]
        digest = hashlib.sha256(attachments[0].read_bytes()).hexdigest()
        assert digest.upper() == case["input_sha256"].upper(), case["id"]
