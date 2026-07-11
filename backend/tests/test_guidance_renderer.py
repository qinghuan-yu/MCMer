from app.guidance.renderer import render_verified_guidance


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
