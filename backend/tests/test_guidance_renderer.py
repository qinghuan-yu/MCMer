from app.guidance.renderer import render_verified_guidance


def test_default_reader_decision_summary_answers_all_reader_questions() -> None:
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

    section = markdown.split("## 建模路线与读者决策", maxsplit=1)[1].split(
        "## 建模路线取舍", maxsplit=1
    )[0]

    assert "观测量/未知量/时间口径" in section
    assert "唯一" in section
    assert "推荐" in section and "排除" in section
    assert "最终可填内容" in section and "result_prediction" in section
    assert "数据证据" in section
    assert "额外假设" in section
    assert "竞赛代表解" in section
