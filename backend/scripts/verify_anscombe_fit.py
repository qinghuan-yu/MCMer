"""Independently check the frozen workbook, published coefficients and their evidence."""
from __future__ import annotations

import argparse
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import sys

from openpyxl import load_workbook


BACKEND = Path(__file__).resolve().parents[1]
CASE = BACKEND / "tests/fixtures/anscombe_linear_fit/case.json"
TOLERANCE = Fraction(5, 100000)


def observations(workbook):
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        rows = iter(book["observations"].values)
        if next(rows) != ("x1", "y1"):
            raise ValueError("unexpected observation columns")
        pairs = [(Fraction(str(x)), Fraction(str(y))) for x, y in rows]
    finally:
        book.close()
    if len(pairs) != 11:
        raise ValueError("expected all eleven observations")
    return pairs


def reference_fit(pairs):
    mean_x, mean_y = (sum(column) / len(pairs) for column in zip(*pairs))
    slope = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / sum((x - mean_x)**2 for x, _ in pairs)
    return {"intercept": mean_y - slope * mean_x, "slope": slope}


def check_fitted_observations(results, pairs, reference, *, supporting_outputs=None):
    evidence = [item["value"] for item in results]
    by_id = {(item.get("subproblem_id"), item.get("id")): item for item in results}
    for item in results:
        ref = item.get("support_ref")
        if not ref:
            continue
        artifact, *fields = item["value_ref"].split(".")
        owner = item["subproblem_id"]
        escaped = [part.replace("~", "~0").replace("/", "~1") for part in (owner, artifact)]
        if ref == "result_registry.json#/supporting_outputs/" + "/".join(escaped):
            source = (supporting_outputs or {}).get(owner, {}).get(artifact)
        elif ref.startswith("result_registry.json#"):
            source = by_id.get((owner, ref.removeprefix("result_registry.json#")))
        else:
            source = None
        if source is None or source.get("source_node") != item["source_node"]:
            raise ValueError("missing or unrelated supporting output for the published field")
        selected = source["value"]
        for field in fields:
            if not isinstance(selected, dict) or field not in selected:
                raise ValueError("published field is absent from its supporting output")
            selected = selected[field]
        if not fields or item["value"] != selected:
            raise ValueError("published field differs from the original fit")
        evidence.append(source["value"])
    fits = [value for value in evidence if isinstance(value, dict) and {"x", "observed", "predicted"} <= value.keys()]
    if not fits:
        raise ValueError("missing observation-level evidence for the published fit")
    for fit in fits:
        rows = zip(fit["x"], fit["observed"], fit["predicted"], strict=True)
        for (x, y), (actual_x, observed_y, fitted_y) in zip(pairs, rows, strict=True):
            if (Fraction(str(actual_x)), Fraction(str(observed_y))) != (x, y):
                raise ValueError("fit uses different observations")
            predicted = reference["intercept"] + reference["slope"] * x
            if abs(Fraction(str(fitted_y)) - predicted) > TOLERANCE:
                raise ValueError("published predictions disagree with the independently fitted line")


def check_published_coefficients(report, results, expected):
    """Each labelled row must match its own registered output and the independent fit."""
    wanted = {}
    mapping_rows = {}
    for result in results:
        value = result["value"]
        if isinstance(value, dict):
            fields = value.get("named_coefficients", value)
            if not isinstance(fields, dict):
                raise ValueError("invalid named coefficient mapping")
            if fields is value and expected.keys() & fields.keys():
                mapping_rows[result["subproblem_id"], result["id"]] = expected.keys() & fields.keys()
        else:
            key = result["value_ref"].rsplit(".", 1)[-1] if result.get("support_ref") else result["id"]
            fields = {key: value}
        for name in expected.keys() & fields.keys():
            key = (result["subproblem_id"], name, result["id"])
            if key in wanted or abs(Fraction(str(fields[name])) - Fraction(str(expected[name]))) > TOLERANCE:
                raise ValueError(f"incorrect or duplicate registered {name}")
            wanted[key] = (Fraction(str(fields[name])), result["status"])
    if {key[1] for key in wanted} != set(expected):
        raise ValueError("both named coefficients must be registered")
    heading = "## 最终答案与应填表格"
    lines = report.splitlines()
    if lines.count(heading) != 1:
        raise ValueError("missing or ambiguous final answer table")
    remaining = dict(wanted)
    published = {}
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("## "):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5:
            continue
        subproblem, name, number, status, ref = cells
        if (subproblem, name) in mapping_rows:
            mapping = json.loads(number)
            entries = {key: mapping[key] for key in mapping_rows[subproblem, name]}
            if ref != f"`result_registry.json#{name}`":
                raise ValueError("coefficient mapping has an incorrect result reference")
        elif name in expected:
            entries = {name: number}
        else:
            continue
        if not ref.startswith("`result_registry.json#") or not ref.endswith("`"):
            raise ValueError(f"missing result reference for {name}")
        for field, value in entries.items():
            key = (subproblem, field, ref[len("`result_registry.json#"):-1])
            if key not in remaining or remaining.pop(key) != (Fraction(str(value)), status):
                raise ValueError(f"unexpected or unbound answer for {field}")
            published[field] = float(value)
    if remaining:
        raise ValueError("the report omits a registered coefficient")
    return published


def audit_run(root):
    from app.guidance.contracts import VerificationReport
    from app.guidance.recovery import workspace_file
    from app.guidance.verification_stage import ResultVerificationStage

    root = Path(root)
    def read(name):
        return json.loads((root / name).read_bytes())

    case = json.loads(CASE.read_bytes())
    item, = case["attachments"]
    workbook = root / "data" / item["file"]
    pairs = observations(CASE.parent / item["file"])
    reference = reference_fit(pairs)
    receipt = {"case_id": case["case_id"], "passed": False, "observations": 11,
               "exact_reference": {name: str(value) for name, value in reference.items()}}
    try:
        catalog = read("input_catalog.json")
        source = workspace_file(root, catalog["source_file"])
        if (sha256(source.read_bytes()).hexdigest() != catalog["source_sha256"] or catalog["source_sha256"] != case["task_sha256"]
                or sha256(workbook.read_bytes()).hexdigest() != item["sha256"]):
            raise ValueError("frozen question or workbook does not match")
        terminal = read("pipeline_events.json")[-1]
        summary = read("run_summary.json")
        if (terminal["type"] != "result" or terminal["data"]["status"] != "completed" or
                summary["status"] != "completed" or summary["pipeline_result"] != terminal["data"]):
            raise ValueError("workflow did not complete with published answers")
        audit = read("guidance_audit_report.json")
        markdown = (root / "guidance.md").read_bytes()
        if (audit["status"] != "PASS" or audit["publication_status"] != "verified"
                or sha256(markdown).hexdigest() != audit["guidance_sha256"]):
            raise ValueError("missing verified publication or report digest mismatch")
        manifest = read("execution_manifest.json")
        if manifest["input_files_sha256"].get("data/" + item["file"]) != item["sha256"]:
            raise ValueError("the executed model did not use the frozen workbook")
        verification = VerificationReport.model_validate(read("verification_report.json"))
        if verification.status != "verified" or verification != ResultVerificationStage.assess(root / "execution_manifest.json", root):
            raise ValueError("current evidence cannot reproduce verification")
        registry = read("result_registry.json")
        results = registry["verified_results"]
        check_fitted_observations(results, pairs, reference, supporting_outputs=registry.get("supporting_outputs"))
        published = check_published_coefficients(markdown.decode("utf-8"), results, reference)
        receipt.update(passed=True, coefficients=published, guidance_sha256=audit["guidance_sha256"])
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        receipt["reason"] = str(error)
    return receipt


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    result = audit_run(parser.parse_args().run)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
