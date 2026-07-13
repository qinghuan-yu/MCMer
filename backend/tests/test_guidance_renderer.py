from app.guidance.renderer import render_verified_guidance


def test_guidance_summary_renders_three_verification_layers_not_single_status() -> None:
    markdown = render_verified_guidance(
        approved_model={
            "review_status": "approved",
            "approved_model_ir": {"model_id": "layered-model", "subproblems": []},
        },
        verification_report={
            "status": "partial",
            "execution_verified": {
                "status": "passed",
                "reasons": ["operator graph completed"],
            },
            "evidence_supported": {
                "status": "passed",
                "reasons": ["results trace to source nodes"],
            },
            "model_adequate": {
                "status": "not_assessed",
                "reasons": ["cross-validation evidence is missing"],
            },
        },
        parameter_registry={"parameters": []},
        result_registry={"verified_results": [], "blocked_results": []},
        execution_manifest={"status": "passed"},
    )

    assert "| 执行正确性 | `passed` |" in markdown
    assert "| 证据可追溯性 | `passed` |" in markdown
    assert "| 模型充分性 | `not_assessed` |" in markdown
    assert "结果复核状态为" not in markdown


def test_verification_failure_recovery_targets_operator_execution_graph() -> None:
    markdown = render_verified_guidance(
        approved_model={
            "review_status": "approved",
            "model_id": "approved-ir",
            "approved_model_ir": {
                "model_id": "approved-ir",
                "subproblems": [
                    {
                        "execution_status": "solvable",
                        "subproblem_id": "problem1",
                        "objective": "Fit the observed response.",
                    }
                ],
            },
        },
        verification_report={
            "status": "blocked",
            "model_id": "approved-ir",
            "blocking_issues": ["residual check failed"],
            "artifact_refs": {
                "approved_model": "approved_model_ir.json",
                "execution_manifest": "execution_manifest.json",
            },
        },
        parameter_registry={"parameters": []},
        result_registry={"verified_results": [], "blocked_results": []},
        execution_manifest={"status": "passed"},
    )

    assert markdown.startswith("# 结果复核阻断诊断")
    assert "execution_plan.json" in markdown
    assert "solve.py" not in markdown
    assert "ModelSpec" not in markdown


def test_renderer_does_not_invent_reader_guidance_when_summary_is_missing() -> None:
    markdown = render_verified_guidance(
        approved_model={
            "approved_model": {
                "selected_method": "interpretable linear trend",
                "candidate_methods": [],
                "subproblem_models": [],
            }
        },
        verification_report={"status": "verified"},
        parameter_registry={"parameters": []},
        result_registry={
            "verified_results": [{"id": "result_prediction"}],
            "blocked_results": [],
            "summary": {},
        },
    )

    assert "## 建模路线与读者决策" not in markdown
    assert "## 建模路线取舍" not in markdown
    assert "## 可辨识性分析" not in markdown
    assert "## 结论状态登记" not in markdown
    assert "## 最终答案与应填表格" not in markdown


def test_renderer_outputs_concrete_rows_from_typed_reader_guidance_summary() -> None:
    markdown = render_verified_guidance(
        approved_model={
            "approved_model": {
                "selected_method": "linear trend with holdout validation",
                "candidate_methods": [],
                "subproblem_models": [],
            }
        },
        verification_report={"status": "verified"},
        parameter_registry={"parameters": []},
        result_registry={
            "verified_results": [
                {
                    "id": "result_prediction",
                    "claim_text": "The next-period demand forecast is 208.",
                }
            ],
            "blocked_results": [],
            "summary": {
                "verified_count": 1,
                "blocked_count": 0,
                "coverage_status": "verified",
                "reader_decision_guide": [],
                "method_route_comparison": [],
                "identifiability_analysis": [],
                "claim_registry": [],
                "final_answer_tables": [
                    {
                        "id": "final_forecast",
                        "title": "下一期预测答案",
                        "rows": [
                            {
                                "subproblem_id": "problem1",
                                "answer_item": "下一期需求",
                                "answer": "208",
                                "status": "evidence_verified",
                                "evidence_ids": ["result_prediction"],
                            }
                        ],
                        "notes": ["该数值依赖线性趋势延续假设。"],
                    }
                ],
            },
        },
    )

    section = markdown.split("## 最终答案与应填表格", maxsplit=1)[1].split(
        "## 分问题建模步骤", maxsplit=1
    )[0]

    assert "下一期预测答案" in section
    assert "下一期需求" in section
    assert "208" in section
    assert "evidence_verified" in section
    assert "result_prediction" in section


def test_renderer_preserves_canonical_reader_decision_topics() -> None:
    topics = [
        "problem_context",
        "identifiability",
        "route_choice",
        "final_answers",
        "claim_sources",
    ]
    markdown = render_verified_guidance(
        approved_model={"approved_model": {"subproblem_models": []}},
        verification_report={"status": "verified"},
        parameter_registry={"parameters": []},
        result_registry={
            "verified_results": [{"id": "result_prediction"}],
            "blocked_results": [],
            "summary": {
                "reader_decision_guide": [
                    {
                        "topic": topic,
                        "question": f"question for {topic}",
                        "judgment": "registered judgment",
                        "action": "registered action",
                    }
                    for topic in topics
                ]
            },
        },
    )

    assert "观测量/未知量/时间口径" in markdown
    assert "可辨识性与缺失信息" in markdown
    assert "推荐路线与排除路线" in markdown
    assert "最终答案、证据与写作动作" in markdown
    assert "数据/证据、假设/先验、候选/代表解来源分层" in markdown


def test_renderer_uses_registered_execution_model_instead_of_stale_candidate_model() -> None:
    markdown = render_verified_guidance(
        approved_model={
            "approved_model": {
                "subproblem_models": [{
                    "execution_status": "solvable",
                    "subproblem_id": "1.2",
                    "objective": "detect a breakpoint",
                    "selected_method": "CUSUM candidate",
                    "rationale": "candidate only",
                    "equations": ["stale power-law formula"],
                    "parameters": [],
                    "constraints": [],
                    "solve_steps": ["run unexecuted CUSUM"],
                    "expected_results": ["candidate"],
                }],
            },
        },
        verification_report={"status": "verified", "artifact_refs": {"figures": []}},
        parameter_registry={"parameters": []},
        result_registry={
            "verified_results": [{
                "id": "result_breakpoint",
                "claim_text": "BIC supports the registered hinge model.",
                "metrics": {"rmse": 1.0, "max_abs_residual": 2.0},
            }],
            "blocked_results": [],
            "summary": {
                "identifiability_analysis": [{
                    "subproblem_id": "1.2-rock",
                    "observed": "five force series by torque",
                    "unknowns": "common Tc and hinge coefficients",
                    "equation": "P_j=a_j+b_j*T+c_j*max(0,T-Tc)",
                    "rank_condition": "fixed-Tc design matrix has full rank",
                    "conclusion": "Tc is a criterion-dependent candidate",
                    "missing_information": "denser observations near the transition",
                    "selection_rule": "select by BIC improvement over a single line",
                    "guidance": "report the candidate interval and evidence ID",
                }],
                "final_answer_tables": [{
                    "id": "answer_1_2",
                    "title": "breakpoint answer",
                    "rows": [{
                        "subproblem_id": "1.2",
                        "answer_item": "rock breakpoint",
                        "answer": "registered candidate interval",
                        "status": "chosen_under_prior",
                        "evidence_ids": ["result_breakpoint"],
                    }],
                    "notes": [],
                }],
            },
        },
    )

    section = markdown.split("## 分问题建模步骤", 1)[1].split("## 必要计算结果", 1)[0]
    assert "P_j=a_j+b_j*T+c_j*max(0,T-Tc)" in section
    assert "select by BIC improvement" in section
    assert "result_breakpoint" in section
    assert "stale power-law formula" not in section
    assert "unexecuted CUSUM" not in section
    assert "stale power-law formula" not in markdown
    assert "unexecuted CUSUM" not in markdown


def test_renderer_explains_figure_with_bound_result_claim() -> None:
    markdown = render_verified_guidance(
        approved_model={"approved_model": {"subproblem_models": []}},
        verification_report={
            "status": "verified",
            "artifact_refs": {"figures": [{
                "path": "figures/bic.png",
                "role": "verification_diagnostic",
                "source_data": ["data/attachment.xlsx"],
                "linked_result_ids": ["result_rock_breakpoint"],
            }]},
        },
        parameter_registry={"parameters": []},
        result_registry={
            "verified_results": [{
                "id": "result_rock_breakpoint",
                "claim_text": "岩石折线 BIC 明显低于单一直线，支持判据下候选区间。",
                "metrics": {"rmse": 1.0, "max_abs_residual": 2.0},
            }],
            "blocked_results": [],
            "summary": {},
        },
    )

    figure_section = markdown.split("## 图片解读", 1)[1].split("## 复核与稳健性", 1)[0]
    assert "岩石折线 BIC 明显低于单一直线" in figure_section
