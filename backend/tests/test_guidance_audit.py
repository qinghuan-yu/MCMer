from app.artifacts.guidance_audit import build_guidance_audit_report, sanitize_unregistered_numeric_claims


def test_guidance_audit_blocks_unregistered_numbers() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | 题设 |\n\n"
            "必要计算结果显示，增长率为 0.37。"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={"verified_results": [], "blocked_results": []},
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": 0.12,
                    "trust_status": "source_locked",
                }
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "unregistered_numeric_claim" for block in report["blocks"])


def test_sanitize_unregistered_numeric_claims_hides_unsupported_values() -> None:
    markdown = "RMSE = 4.80，周期上限为 240。"
    sanitized = sanitize_unregistered_numeric_claims(
        markdown,
        {
            "blocks": [
                {
                    "type": "unregistered_numeric_claim",
                    "numbers": ["4.8", "240"],
                }
            ]
        },
    )

    assert "4.80" not in sanitized
    assert "240" not in sanitized
    assert "未注册数值" in sanitized


def test_guidance_audit_accepts_registered_numbers() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha = 0.12 | param_alpha |\n\n"
            "必要计算结果显示，预测值为 0.37。"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={
            "verified_results": [
                {
                    "id": "result_prediction",
                    "value": 0.37,
                    "status": "verified",
                }
            ],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": 0.12,
                    "trust_status": "source_locked",
                }
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_blocks_empty_pre_model_guidance() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `blocked`。\n\n"
            "## 参数与来源表\n\n"
            "| parameter_id | 符号 | 含义 |\n| --- | --- | --- |\n\n"
            "## 必要计算结果\n\n"
            "当前没有通过注册表验证的数值结果。\n"
        ),
        guidance_context={"required_sections": ["参数与来源表", "必要计算结果"]},
        result_registry={
            "verified_results": [],
            "blocked_results": [{"id": "blocked_before_model_review", "reason": "model timeout"}],
        },
        parameter_registry={"parameters": []},
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "no_verified_guidance_evidence" for block in report["blocks"])


def test_guidance_audit_blocks_formal_guidance_missing_reader_facing_sections() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha = 0.12 | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "预测值为 0.37。\n"
        ),
        guidance_context={
            "required_sections": [
                "参数与来源表",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "value": 0.37}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": 0.12},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "missing_expected_section" for block in report["blocks"])


def test_guidance_audit_blocks_formal_guidance_with_vague_reader_sections() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "系统已完成建模。\n\n"
            "## 建模路线与读者决策\n\n"
            "本方案已经完成，可参考注册表。\n\n"
            "## 建模路线取舍\n\n"
            "推荐当前方法，效果较好。\n\n"
            "## 可辨识性分析\n\n"
            "模型通过复核。\n\n"
            "## 结论状态登记\n\n"
            "结论可信。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "答案见 result_prediction。\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "vague_reader_guidance" for block in report["blocks"])


def test_guidance_audit_blocks_identifiability_section_without_structural_analysis() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "模型通过复核，因此可唯一恢复。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；design matrix rank full；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "identifiability_section_missing_structure" for block in report["blocks"])


def test_guidance_audit_blocks_route_tradeoff_section_without_reason_boundary_or_evidence() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check；"
            "可填内容来自 result_prediction，结论来源分层包含 data evidence、assumption 和 representative contest answer。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数为 alpha,beta，dimension two；design matrix rank full，"
            "因此 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "route_tradeoff_section_missing_structure" for block in report["blocks"])


def test_guidance_audit_blocks_problem_understanding_without_observation_unknown_or_time_context() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "系统已完成建模指导。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "problem_understanding_missing_context" for block in report["blocks"])


def test_guidance_audit_blocks_data_section_without_observed_source_or_time_context() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 数据与来源\n\n"
            "数据来源逐项记录在参数注册表和结果注册表中。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| claim_prediction | evidence_verified | 数据自洽预测值 | result_prediction | linear trend assumption | representative contest answer |\n\n"
            "## 参数与来源表\n\n"
            "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| param_alpha | alpha | trend intercept | estimated_alpha | demand_unit | source_data | demand_table | evidence_verified |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；parameter 来自 param_alpha；"
            "solver 使用 least squares fit；result_prediction 登记了 metric rmse 与 residual summary。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n\n"
            "## 复核与稳健性\n\n"
            "RMSE 与 residual metric 使用 holdout baseline threshold 判断；解释为模型预测误差在容许准则内，"
            "不能代表真实机理证明；robustness and sensitivity 仍受 linear trend boundary 限制。\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "数据与来源",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
                "复核与稳健性",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": "estimated_alpha",
                    "unit": "demand_unit",
                    "source_type": "source_data",
                    "source_ref": "demand_table",
                    "trust_status": "evidence_verified",
                },
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "data_section_missing_observation_context" for block in report["blocks"])


def test_guidance_audit_blocks_claim_registry_without_status_evidence_or_assumption_layers() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "结论可信。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "claim_registry_section_missing_structure" for block in report["blocks"])


def test_guidance_audit_blocks_calculation_results_without_formula_parameter_solver_or_metric_context() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| claim_prediction | evidence_verified | 数据自洽预测值 | result_prediction | linear trend assumption | representative contest answer |\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "`result_prediction` 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "calculation_results_section_missing_structure" for block in report["blocks"])


def test_guidance_audit_blocks_subproblem_steps_without_actionable_modeling_details() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| claim_prediction | evidence_verified | 数据自洽预测值 | result_prediction | linear trend assumption | representative contest answer |\n\n"
            "## 参数与来源表\n\n"
            "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| param_alpha | alpha | trend intercept | estimated_alpha | demand_unit | source_data | demand_table | evidence_verified |\n\n"
            "## 分问题建模步骤\n\n"
            "系统已完成各子问题建模，详见注册表。\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；parameter 来自 param_alpha；"
            "solver 使用 least squares fit；result_prediction 登记了 metric rmse 与 residual summary。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n\n"
            "## 复核与稳健性\n\n"
            "RMSE 与 residual metric 使用 holdout baseline threshold 判断；解释为模型预测误差在容许准则内，"
            "不能代表真实机理证明；robustness and sensitivity 仍受 linear trend boundary 限制。\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "分问题建模步骤",
                "必要计算结果",
                "最终答案与应填表格",
                "复核与稳健性",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": "estimated_alpha",
                    "unit": "demand_unit",
                    "source_type": "source_data",
                    "source_ref": "demand_table",
                    "trust_status": "evidence_verified",
                },
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "subproblem_steps_missing_actionable_modeling" for block in report["blocks"])


def test_guidance_audit_blocks_parameter_table_without_value_unit_source_or_trust_context() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| claim_prediction | evidence_verified | 数据自洽预测值 | result_prediction | linear trend assumption | representative contest answer |\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；parameter alpha 绑定 param_alpha；"
            "solver 使用 least squares fit；metric RMSE 和 result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": 12.0,
                    "unit": "vehicles/sample",
                    "source_type": "estimated_from_data",
                    "source_ref": "data/forecast.xlsx",
                    "trust_status": "evidence_verified",
                },
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "parameter_table_missing_source_structure" for block in report["blocks"])


def test_guidance_audit_blocks_review_section_without_error_threshold_or_interpretation() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "| claim_id | 状态 | 结论 | 证据 | 依赖假设 | 风险 |\n| --- | --- | --- | --- | --- | --- |\n"
            "| claim_prediction | evidence_verified | 数据自洽预测值 | result_prediction | linear trend assumption | representative contest answer |\n\n"
            "## 参数与来源表\n\n"
            "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| param_alpha | alpha | trend slope | 12.0 | vehicles/sample | estimated_from_data | data/forecast.xlsx | evidence_verified |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；parameter alpha 绑定 param_alpha；"
            "solver 使用 least squares fit；metric RMSE 和 result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n\n"
            "## 复核与稳健性\n\n"
            "- 数值复核结论：`verified`。\n"
            "- 残差/目标值复核：1/1 项通过。\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
                "复核与稳健性",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result", "metrics": {"rmse": 0.5}}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": 12.0,
                    "unit": "vehicles/sample",
                    "source_type": "estimated_from_data",
                    "source_ref": "data/forecast.xlsx",
                    "trust_status": "evidence_verified",
                },
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "review_section_missing_error_context" for block in report["blocks"])


def test_guidance_audit_blocks_final_answer_section_without_evidence_binding() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "线性方程 design matrix rank full，因此参数可辨识且 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 |\n| --- | --- |\n| 下一期需求 | 使用线性趋势外推值 |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "final_answer_missing_evidence_binding" for block in report["blocks"])


def test_guidance_audit_blocks_final_answer_table_rows_without_own_evidence() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "线性方程 design matrix rank full，因此参数可辨识且 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 证据 |\n| --- | --- | --- |\n"
            "| 下一期需求 | 使用线性趋势外推值 | result_prediction |\n"
            "| 异常情景需求 | 采用同一趋势外推 |  |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "final_answer_missing_evidence_binding" for block in report["blocks"])


def test_guidance_audit_blocks_unregistered_final_answer_evidence_ids() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "线性方程 design matrix rank full，因此参数可辨识且 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 证据 |\n| --- | --- | --- |\n"
            "| 下一期需求 | 使用线性趋势外推值 | result_missing |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "final_answer_unknown_evidence_reference" for block in report["blocks"])


def test_guidance_audit_blocks_final_answer_rows_without_claim_status() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "线性方程 design matrix rank full，因此参数可辨识且 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 证据 |\n| --- | --- | --- |\n"
            "| 下一期需求 | 使用线性趋势外推值 | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "final_answer_missing_claim_status" for block in report["blocks"])


def test_guidance_audit_blocks_final_answer_rows_that_only_point_to_registry() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "推荐 interpretable linear trend with holdout verification；被排除路线是 black-box forecast without holdout check。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: linear trend；rejected route: black-box forecast，因为无法解释验证误差。\n\n"
            "## 可辨识性分析\n\n"
            "线性方程 design matrix rank full，因此参数可辨识且 unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据来自 result_prediction；额外 assumption 是 linear trend；候选 representative answer 用于竞赛填表。\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | param_alpha |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；solver 使用 least squares fit；result_prediction 已登记。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | 见 result_prediction | evidence_verified | result_prediction |\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": "source-locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "final_answer_missing_concrete_answer" for block in report["blocks"])


def test_guidance_audit_blocks_figure_section_without_interpretation_or_limitation() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 可信度摘要\n\n"
            "结果复核状态为 `verified`。\n\n"
            "## 问题理解\n\n"
            "观测数据是历史需求表，未知目标是下一期需求，时间口径是 period index。\n\n"
            "## 建模路线与读者决策\n\n"
            "observed data 是历史需求表；unknown target 是下一期需求；time coordinate 使用 period index。"
            "identifiability 由 design matrix rank 判定，missing information 会单独披露。"
            "recommended route 是 interpretable linear trend，rejected route 是 black-box forecast。"
            "model formula、parameter、solver fit 和 final answer 均绑定 result_prediction。"
            "data evidence、assumption prior 和 representative contest candidate answer 分层登记。\n\n"
            "## 建模路线取舍\n\n"
            "recommended route: interpretable linear trend；rejected route: black-box forecast；"
            "reason: holdout error and interpretability；applicability boundary: linear trend period data only；"
            "evidence source: result_prediction and data table。\n\n"
            "## 可辨识性分析\n\n"
            "观测方程 demand = alpha + beta * period；未知参数 alpha,beta，dimension two；"
            "design matrix rank full；therefore unique under trend assumption。\n\n"
            "## 结论状态登记\n\n"
            "数据证据 result_prediction 的 status 为 evidence_verified；额外 assumption 是 linear trend；"
            "candidate representative answer 用于竞赛填表，risk 为模型形式边界。\n\n"
            "## 参数与来源表\n\n"
            "| parameter_id | 符号 | 含义 | 数值 | 单位 | 来源类型 | 来源 | 可信状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| param_alpha | alpha | trend intercept | estimated_alpha | demand_unit | source_data | demand_table | evidence_verified |\n\n"
            "## 必要计算结果\n\n"
            "model formula: demand = alpha + beta * period；parameter 来自 param_alpha；"
            "solver 使用 least squares fit；result_prediction 登记了 metric rmse 与 residual summary。\n\n"
            "## 最终答案与应填表格\n\n"
            "| 应填项 | 答案 | 结论状态 | 证据 |\n| --- | --- | --- | --- |\n"
            "| 下一期需求 | demand_next = alpha + beta * next_period | evidence_verified | result_prediction |\n\n"
            "## 图片解读\n\n"
            "![result_fit](figures/fit.png)\n"
            "- 图片 `figures/fit.png`；用途 `result_fit`；来源数据：`data/demand.xlsx`；绑定结果：`result_prediction`。\n\n"
            "## 复核与稳健性\n\n"
            "RMSE 与 residual metric 使用 holdout baseline threshold 判断；解释为模型预测误差在容许准则内，"
            "不能代表真实机理证明；robustness and sensitivity 仍受 linear trend boundary 限制。\n"
        ),
        guidance_context={
            "required_sections": [
                "问题理解",
                "建模路线与读者决策",
                "建模路线取舍",
                "可辨识性分析",
                "结论状态登记",
                "参数与来源表",
                "必要计算结果",
                "最终答案与应填表格",
                "图片解读",
                "复核与稳健性",
            ]
        },
        result_registry={
            "verified_results": [{"id": "result_prediction", "claim_text": "registered result"}],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {
                    "id": "param_alpha",
                    "symbol": "alpha",
                    "value": "estimated_alpha",
                    "unit": "demand_unit",
                    "source_type": "source_data",
                    "source_ref": "demand_table",
                    "trust_status": "evidence_verified",
                },
            ]
        },
        figure_registry={
            "figures": [
                {
                    "path": "figures/fit.png",
                    "role": "result_fit",
                    "source_data": ["data/demand.xlsx"],
                    "linked_result_ids": ["result_prediction"],
                }
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "figure_section_missing_interpretation" for block in report["blocks"])


def test_guidance_audit_treats_blocking_diagnostic_as_diagnostic_artifact() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 结果复核阻断诊断\n\n"
            "## 可信度摘要\n\n"
            "本次任务已有计算产物，但结果复核未通过，因此不会输出正式建模指导方案。\n\n"
            "## 阻断位置\n\n"
            "- 失败阶段：`verification`\n\n"
            "## 阻断原因\n\n"
            "- problem3: recorded nonnegative constraints fail\n\n"
            "## 复核检查概览\n\n"
            "- 约束满足复核：0/1 项通过。\n\n"
            "## 下一步修复动作\n\n"
            "- 回到 solve.py 与 solver_results.json 修正 typed solver evidence。\n\n"
            "## 可复现附件说明\n\n"
            "- `result_registry`：`result_registry.json`\n"
        ),
        guidance_context={
            "required_sections": [
                "可信度摘要",
                "阻断位置",
                "阻断原因",
                "复核检查概览",
                "下一步修复动作",
                "可复现附件说明",
            ]
        },
        result_registry={
            "verified_results": [{"id": "problem3", "value": {"rmse": 24.7}}],
            "blocked_results": [{"id": "problem1", "reason": "Optimization failed"}],
        },
        parameter_registry={
            "parameters": [
                {"id": "theta_problem3", "symbol": "theta_3", "value": [1, 2, 3]},
            ]
        },
        figure_registry={
            "figures": [
                {
                    "path": "result_fit_prob3.png",
                    "role": "result_fit",
                    "source_data": ["data/附件(Attachment).xlsx"],
                    "linked_result_ids": ["problem3"],
                }
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_ignores_decimal_section_numbers() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha = 0.12 | param_alpha |\n\n"
            "### 1.1 问题背景\n\n"
            "### 10.2 必须补算的部分\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={"verified_results": [], "blocked_results": []},
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": 0.12, "trust_status": "source_locked"},
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_accepts_numbers_from_disclosed_blocked_results() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha = 0.12 | param_alpha |\n\n"
            "blocked: 支路2在 t=50 时出现 -0.7554，必须重算，不能直接引用。\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={
            "verified_results": [],
            "blocked_results": [{"id": "blocked_q2", "value": {"t": 50, "f2": -0.7554}}],
            "warning_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": 0.12, "trust_status": "source_locked"},
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_accepts_rounded_registered_numbers() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| a1 = 0.2819 | param_a1 |\n\n"
            "verified: 支路1斜率 a1 = 0.2819。\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={
            "verified_results": [
                {"id": "q1_parameters", "value": {"a1": 0.28189592730819596}},
            ],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_a1", "symbol": "a1", "value": 0.28189592730819596, "trust_status": "estimated"},
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_accepts_numbers_embedded_in_registered_strings() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| t_range | param_t_range |\n\n"
            "source-locked: 时间范围 t = 0, 1, 2, ..., 59。\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={
            "verified_results": [
                {"id": "q1_parameters", "inputs": {"t_range": "时间范围 t = 0, 1, 2, ..., 59"}},
            ],
            "blocked_results": [],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_t_range", "symbol": "t_range", "value": "时间范围 t = 0, 1, 2, ..., 59", "trust_status": "source_locked"},
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []


def test_guidance_audit_blocks_missing_parameter_coverage() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | 题设 |\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={"verified_results": [], "blocked_results": []},
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": 0.12, "trust_status": "source_locked"},
                {"id": "param_beta", "symbol": "beta", "value": None, "trust_status": "assumed"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "missing_parameter_registry_coverage" for block in report["blocks"])


def test_guidance_audit_blocks_undisclosed_blocked_results() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# 建模指导方案\n\n"
            "## 参数与来源表\n\n"
            "| 参数 | 来源 |\n| --- | --- |\n| alpha | 题设 |\n"
        ),
        guidance_context={"required_sections": ["参数与来源表"]},
        result_registry={
            "verified_results": [],
            "blocked_results": [{"id": "result_q2_failed", "reason": "missing input"}],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "trust_status": "source_locked"},
            ]
        },
    )

    assert report["status"] == "BLOCK"
    assert any(block["type"] == "blocked_items_not_disclosed" for block in report["blocks"])


def test_guidance_audit_ignores_numbers_inside_urls() -> None:
    report = build_guidance_audit_report(
        guidance_markdown=(
            "# Guidance\n\n"
            "## Parameters\n\n"
            "| parameter | source |\n| --- | --- |\n| alpha = 0.12 | param_alpha |\n\n"
            "blocked: Schema validation failed. See "
            "https://errors.pydantic.dev/2.13/v/dict_type for parser details.\n"
        ),
        guidance_context={"required_sections": ["Parameters"]},
        result_registry={
            "verified_results": [],
            "blocked_results": [
                {"id": "blocked_schema", "reason": "Schema validation failed"},
            ],
        },
        parameter_registry={
            "parameters": [
                {"id": "param_alpha", "symbol": "alpha", "value": 0.12, "trust_status": "source_locked"},
            ]
        },
    )

    assert report["status"] == "PASS"
    assert report["blocks"] == []
