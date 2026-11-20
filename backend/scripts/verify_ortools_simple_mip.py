"""只读核验 OR-Tools 固定例题的终态、发布证据和数学结果。"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from fractions import Fraction
from hashlib import sha256
from itertools import product
import json
from math import floor, isfinite
from pathlib import Path
import sys


CASE_ID = "ortools_simple_mip"
TASK_SHA256 = "25c0c88c8a62a759ca829fb8109f6e0b473d4850181a155aad38d837449cef0e"
TOLERANCE = Fraction(1, 10**7)


def number(value):
    if type(value) not in (int, float) or (isinstance(value, float) and not isfinite(value)):
        raise ValueError("expected a finite number")
    return Fraction(str(value))


def check_solution(solution, x_key, y_key):
    """核对两个整数列的题面约束、完整可行集、最优解与报告数值。"""
    model = solution["compiled_model"]
    labels = model["variable_labels"]
    if len({x_key, y_key, "objective_value"}) != 3 or len(labels) != 2 or set(labels) != {x_key, y_key}:
        raise ValueError("this audit certifies exactly two distinct mapped decision columns")
    if set(solution["decision_values"]) != set(labels) or [number(i) for i in model["integrality"]] != [1, 1]:
        raise ValueError("both decisions must be registered integer columns")
    x_col, y_col = labels.index(x_key), labels.index(y_key)

    def coefficients(row):
        if len(row) != 2:
            raise ValueError("expected two coefficients in compiled column order")
        return number(row[x_col]), number(row[y_col])

    if model["objective_sense"] != "maximize" or coefficients(model["objective"]) != (1, 10):
        raise ValueError("compiled objective must maximize x + 10y")

    inequalities = []
    def add_bounds(a, b, lower, upper):
        if lower is not None:
            inequalities.append((-a, -b, -number(lower)))
        if upper is not None:
            inequalities.append((a, b, number(upper)))

    for label, low, high in zip(labels, model["lower_bounds"], model["upper_bounds"], strict=True):
        add_bounds(int(label == x_key), int(label == y_key), low, high)
    for row, low, high in zip(model["matrix"], model["constraint_lower"], model["constraint_upper"], strict=True):
        add_bounds(*coefficients(row), low, high)

    # 统一写成 ax+by<=c。对具有整数系数的原始左端，可用 floor(c) 检验整数域等价界。
    normalized = set()
    for a, b, rhs in inequalities:
        scale = abs(a or b)
        if scale:
            normalized.add((a / scale, b / scale, floor(rhs / scale)))
    source_bounds = {(-1, 0, 0), (0, -1, 0), (1, 0, 3), (1, 7, 17)}
    if not source_bounds <= normalized:
        raise ValueError("could not certify the two source inequalities and nonnegative domains")
    # x<=3.5、x>=0 和 7y<=17.5 给出整个枚举范围。额外约束也必须保留每个原可行点。
    feasible = [(x, y) for x, y in product(range(4), range(3)) if 2*x + 14*y <= 35 and 2*x <= 7]
    if any(a*x + b*y > rhs for x, y in feasible for a, b, rhs in inequalities):
        raise ValueError("compiled bounds exclude an original feasible point")
    optimum = max(x + 10*y for x, y in feasible)
    optimal_points = [(x, y) for x, y in feasible if x + 10*y == optimum]
    x, y = (number(solution["decision_values"][key]) for key in (x_key, y_key))
    objective = number(solution["objective_value"])
    if abs(x - round(x)) > TOLERANCE or abs(y - round(y)) > TOLERANCE:
        raise ValueError("published decision is not integral")
    if (round(x), round(y)) not in optimal_points or abs(objective - optimum) > TOLERANCE:
        raise ValueError("published result does not match the exhaustive optimum")
    return {"feasible_point_count": len(feasible), "optimum": optimum, "optimal_points": [list(p) for p in optimal_points],
            "constraint_slacks": [float(Fraction(35, 2) - x - 7*y), float(Fraction(7, 2) - x)]}


def check_answer_table(markdown, result, keys, registered_results):
    lines = markdown.splitlines()
    heading = "## 最终答案与应填表格"
    if lines.count(heading) != 1:
        raise ValueError("missing or ambiguous final answer table")
    expected = {"objective_value": result["value"]["objective_value"], **result["value"]["decision_values"]}
    expected_rows = {key: [] for key in keys}
    for registered in registered_results:
        if registered["subproblem_id"] != result["subproblem_id"]:
            continue
        value = registered["value"]
        if isinstance(value, dict) and "objective_value" in value and isinstance(value.get("decision_values"), dict):
            fields = {"objective_value": value["objective_value"], **value["decision_values"]}
        else:
            fields = {registered["id"]: value}
        for key in expected_rows.keys() & fields.keys():
            if number(fields[key]) != number(expected[key]):
                raise ValueError(f"final answer table has conflicting registered values for {key}")
            expected_rows[key].append([registered["subproblem_id"], key, str(fields[key]), registered["status"],
                                       f"`result_registry.json#{registered['id']}`"])
    rows = []
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("#"):
            break
        if line.startswith("|") and line.endswith("|"):
            rows.append([cell.strip() for cell in line[1:-1].split("|")])
    for key in keys:
        matches = [row for row in rows if row[:2] == [result["subproblem_id"], key]]
        evidence_ids = {row[-1] for row in expected_rows[key]}
        if len(evidence_ids) != len(expected_rows[key]) or sorted(matches) != sorted(expected_rows[key]):
            raise ValueError(f"final answer table contains a missing, contradictory or unbound value for {key}")


def audit_run(root: Path, result_id: str, x_key: str, y_key: str):
    """只评价给定运行目录；是否真实调用模型另由调用账本证明。"""
    from app.guidance.contracts import VerificationReport
    from app.guidance.recovery import workspace_file
    from app.guidance.verification_stage import ResultVerificationStage

    def read(name):
        return json.loads((root / name).read_bytes())

    receipt = {"case_id": CASE_ID, "scope": "terminal status, published evidence and independent mathematics",
               "task_sha256": TASK_SHA256, "result_id": result_id, "mapping": {"x": x_key, "y": y_key},
               "passed": False, "failures": []}
    try:
        catalog = read("input_catalog.json")
        source = workspace_file(root, catalog["source_file"]).read_bytes()
        if sha256(source).hexdigest() != TASK_SHA256 or catalog["source_sha256"] != TASK_SHA256:
            raise ValueError("run did not use the frozen OR-Tools task")
        summary = read("run_summary.json")
        events = read("pipeline_events.json")
        if (not events or events[-1]["type"] != "result" or events[-1]["data"]["status"] != "completed"
                or summary["status"] != "completed" or summary["pipeline_result"] != events[-1]["data"]):
            raise ValueError("matching completed pipeline and terminal summary required")
        receipt["terminal_status"] = "completed"
        audit = read("guidance_audit_report.json")
        guidance = (root / "guidance.md").read_bytes()
        digest = sha256(guidance).hexdigest()
        if audit["status"] != "PASS" or audit["publication_status"] != "verified" or digest != audit["guidance_sha256"]:
            raise ValueError("verified publication with matching report digest required")
        verification = VerificationReport.model_validate(read("verification_report.json"))
        if verification.status != "verified" or ResultVerificationStage.assess(root / "execution_manifest.json", root) != verification:
            raise ValueError("current evidence does not support published verification")
        receipt.update(publication_status=audit["publication_status"], guidance_sha256=digest)
        registered = read("result_registry.json")["verified_results"]
        matches = [r for r in registered if r["id"] == result_id]
        if len(matches) != 1:
            raise ValueError("result ID must identify exactly one verified result")
        result, = matches
        solution = result["value"]
        reference = check_solution(solution, x_key, y_key)
        check_answer_table(guidance.decode("utf-8"), result, ("objective_value", x_key, y_key), registered)
        receipt.update(passed=True, decisions={"x": solution["decision_values"][x_key], "y": solution["decision_values"][y_key]},
                       objective=solution["objective_value"], independent=reference)
    except OSError as error:
        receipt["failures"].append(f"{type(error).__name__}: {Path(error.filename).name if error.filename else 'artifact'}")
    except (ValueError, LookupError, TypeError) as error:
        receipt["failures"].append(str(error))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-run", type=Path, required=True)
    parser.add_argument("--result-id", required=True)
    parser.add_argument("--x-key", required=True)
    parser.add_argument("--y-key", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with redirect_stdout(sys.stderr):
        receipt = audit_run(args.audit_run, args.result_id, args.x_key, args.y_key)
    print(json.dumps(receipt, ensure_ascii=True, allow_nan=False))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
