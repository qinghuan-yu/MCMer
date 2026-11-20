"""从公开的原始观测核算回归直线，脚本模型只提供建模及审核动作。"""
import asyncio
import json
from fractions import Fraction
from pathlib import Path

import pytest

from app.guidance import workflow
from math_tool_fixtures import scripted_model_reply
from scripts.run_closeout import prepare_run


CASE = Path(__file__).parent / "fixtures/anscombe_linear_fit/case.json"


class OfflineRegression:
    def __init__(self, **settings):
        self.__dict__.update(settings)

    async def chat(self, *, agent_name, history, **kwargs):
        context = json.loads(history[1]["content"])
        if agent_name == "GuidanceDecomposerStructure":
            reply = {"subproblems": [{"id": "line", "objective": "估计带截距直线",
                                      "depends_on_subproblem_ids": []}]}
        elif agent_name == "GuidanceDecomposerFacts":
            block = context["input_catalog"]["blocks"][0]
            reply = {"locked_facts": [{"fact_id": "sample", "semantic_key": "sample_size",
                     "value": 11, "unit": "对", "statement": block["text"],
                     "source_refs": [block["source_ref"]], "subproblem_ids": ["line"]}], "uncertainties": []}
        elif agent_name == "GuidanceModeler":
            file = "data/observations.xlsx"
            reply = {"model_id": "ols", "subproblems": [{
                "subproblem_id": "line", "execution_status": "solvable", "objective": "估计截距与斜率",
                "execution_components": ["operator_graph"], "assumptions": [],
                "variables": [{"id": "table", "role": "observed", "symbol": "D", "unit": "unspecified",
                               "shape": ["row", "column", "table"]}],
                "data": [{"id": "observations", "source_ref": file, "fields": {"x": "x1", "y": "y1"}}],
                "operators": [
                    {"node_id": "read", "operator_id": "data.read_excel_table", "inputs": {"source": file},
                     "parameters": {"sheet": "observations"}, "outputs": ["table"], "source_fact_ids": ["sample"]},
                    {"node_id": "estimate", "operator_id": "regression.basis_least_squares",
                     "inputs": {"x": "table.x1", "y": "table.y1"}, "outputs": ["line_fit"],
                     "parameters": {"basis": [{"kind": "constant"}, {"kind": "identity"}]}},
                ],
                "candidate_models": [{"id": role, "role": role, "operator_node_ids": ["read", "estimate"],
                      "selection_metric": "rmse", "applicability_boundary": "观测范围内与均值基线比较。"}
                     for role in ("baseline", "recommended")],
                "validation": {"model_families": ["regression"], "identifiability_checks": ["design_rank"],
                               "evidence_checks": ["residuals"], "robustness_checks": ["leave_one_out"]},
                "final_answers": [{"id": "fit", "item": "截距与斜率", "value_ref": "line_fit",
                                   "status": "evidence_verified", "evidence_ids": ["estimate"]}],
            }]}
        elif agent_name == "GuidanceModelReviewer_line":
            base = "/execution_components/0/operators"
            reply = {"subproblem_id": "line", "status": "approved", "mathematical_checks": [
                {"requirement_id": key, "model_paths": [path], "status": "passed",
                 "rationale": "核对题目指定的工作表、列和带截距拟合。"}
                for key, path in [("sample", base + "/0"), ("$objective", base + "/1"),
                                  ("$formulation", base + "/1")]]}
        else:
            raise AssertionError(agent_name)
        return scripted_model_reply(reply, kwargs)


def test_frozen_observations_and_hand_calculation():
    from scripts.verify_anscombe_fit import observations, reference_fit

    expected = reference_fit(observations(CASE.parent / "observations.xlsx"))
    assert expected == {"intercept": Fraction(33001, 11000), "slope": Fraction(5501, 11000)}


def test_matching_coefficients_cannot_certify_a_fit_to_other_observations():
    from scripts.verify_anscombe_fit import check_fitted_observations

    pairs = [(Fraction(1), Fraction(3)), (Fraction(2), Fraction(5))]
    output = {"x": [1, 2], "observed": [3, 5], "predicted": [3, 5]}
    expected = {"intercept": Fraction(1), "slope": Fraction(2)}
    check_fitted_observations([{"value": output}], pairs, expected)
    for wrong in [{"observed": [4, 4]}, {"predicted": [5, 3]}, {"x": [2, 1]}, {"x": [1, 2, 3]}]:
        with pytest.raises(ValueError):
            check_fitted_observations([{"value": {**output, **wrong}}], pairs, expected)


def test_long_fit_evidence_keeps_named_coefficients_in_the_answer_table():
    from app.guidance.renderer import render_verified_guidance
    from scripts.verify_anscombe_fit import check_published_coefficients

    expected = {"intercept": 4.0, "slope": -0.75}
    result = {"id": "line_fit", "subproblem_id": "line", "status": "evidence_verified",
              "source_node": "estimate", "value": {"predicted": [4.0] * 500, "named_coefficients": expected}}
    markdown = render_verified_guidance(approved_model={}, verification_report={"status": "verified"},
                                        parameter_registry={}, result_registry={"verified_results": [result]})
    assert check_published_coefficients(markdown, [result], expected) == expected


def test_small_published_coefficient_mapping_can_also_be_verified():
    from app.guidance.renderer import render_verified_guidance
    from scripts.verify_anscombe_fit import check_published_coefficients

    expected = {"intercept": 2.5, "slope": -0.3}
    result = {"id": "coefficients", "subproblem_id": "line", "status": "evidence_verified",
              "source_node": "project", "value": expected}
    report = render_verified_guidance(approved_model={}, verification_report={"status": "verified"},
                                      parameter_registry={}, result_registry={"verified_results": [result]})
    assert check_published_coefficients(report, [result], expected) == expected
    with pytest.raises(ValueError):
        check_published_coefficients(report.replace('"slope": -0.3', '"slope": -0.4'), [result], expected)


def test_projected_answers_publish_the_fields_and_keep_the_fit_as_support():
    from app.guidance.renderer import render_verified_guidance
    from scripts.verify_anscombe_fit import check_fitted_observations, check_published_coefficients

    expected = {"slope": 2, "intercept": 1}
    fit = {"x": [1, 2], "observed": [3, 5], "predicted": [3, 5], "named_coefficients": expected}
    support = {"line": {"fit": {"source_node": "estimate", "value": fit}}}
    results = [{"id": "answer_" + field, "subproblem_id": "line", "status": "evidence_verified",
                "source_node": "estimate", "value": value, "value_ref": "fit.named_coefficients." + field,
                "support_ref": "result_registry.json#/supporting_outputs/line/fit"} for field, value in expected.items()]
    report = render_verified_guidance(approved_model={}, verification_report={"status": "verified"},
        parameter_registry={}, result_registry={"verified_results": results, "supporting_outputs": support})
    for field, value in expected.items():
        assert f"| line | {field} | {value} |" in report
    assert check_published_coefficients(report, results, expected) == expected
    rows = [(1, 3), (2, 5)]
    check_fitted_observations(results, rows, expected, supporting_outputs=support)
    for invalid_support in ({}, {"other": support["line"]}, {"line": {"fit": {"source_node": "other", "value": fit}}}):
        with pytest.raises(ValueError):
            check_fitted_observations(results, rows, expected, supporting_outputs=invalid_support)


def test_source_count_authorizes_only_the_approved_assumption_cell():
    from app.artifacts.guidance_audit import build_guidance_audit_report
    from app.guidance.guidance_stage import _assumption_number_context

    statement = "The fit uses 11 observations."
    fact = {"fact_id": "count", "value": 11, "source_refs": ["question#p1"]}
    sub = {"subproblem_id": "s", "fact_bindings": [{"fact_id": "count", "model_paths": ["/execution_components/0"]}],
           "assumptions": [{"statement": statement, "source_refs": ["question#p1"]}],
           "final_answers": [{"id": "line"}]}
    result = {"id": "line", "subproblem_id": "s", "value": 2.5}
    registry = {"verified_results": [result]}
    text = "## 结论状态登记\n\n| line | verified | registered value | line | " + statement + " | scoped |"
    def unknown(doc, source=fact):
        context = _assumption_number_context({"locked_facts": [source]}, {"subproblems": [sub]}, registry)
        audit = build_guidance_audit_report(guidance_markdown=doc, guidance_context={}, result_registry=registry,
                                            assumption_number_context=context)
        return {n for b in audit["blocks"] if b["type"] == "unregistered_numeric_claim" for n in b["numbers"]}
    assert not unknown(text)
    assert unknown(text.replace("registered value", "estimated slope = 11")) == {"11"}
    assert unknown(text.replace(statement, "There were 19 observations.")) == {"19"}
    assert unknown(text.replace("## 结论状态登记", "## 其他")) == {"11"}
    assert unknown(text, {**fact, "source_refs": ["unrelated#p8"]}) == {"11"}


def test_workbook_to_published_coefficients_and_tampered_table(tmp_path, monkeypatch):
    from scripts.verify_anscombe_fit import audit_run, check_published_coefficients

    run = tmp_path / "run"
    task = prepare_run(CASE, run)
    monkeypatch.setattr(workflow, "LLM", OfflineRegression)

    async def events():
        return [event async for event in workflow.build_default_guidance_pipeline().run(run.name, task)]

    stream = asyncio.run(events())
    terminal = stream[-1]["data"]
    (run / "pipeline_events.json").write_text(json.dumps(stream), encoding="utf-8")
    (run / "run_summary.json").write_text(json.dumps({"status": terminal["status"], "pipeline_result": terminal}), encoding="utf-8")
    assert terminal["status"] == "completed", (run / "guidance.md").read_text(encoding="utf-8")
    receipt = audit_run(run)
    assert receipt["passed"], receipt
    report = (run / "guidance.md").read_text(encoding="utf-8")
    registry = json.loads((run / "result_registry.json").read_bytes())["verified_results"]
    expected = receipt["coefficients"]
    rows = [line for line in report.splitlines() if "| slope |" in line]
    assert len(rows) == 1
    for changed in ("", rows[0].replace("slope", "intercept"), rows[0].replace("result_registry.json#fit", "result_registry.json#wrong"),
                    rows[0].replace(rows[0].split("|")[3].strip(), "0.55")):
        with pytest.raises(ValueError):
            check_published_coefficients(report.replace(rows[0], changed), registry, expected)
