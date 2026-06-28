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
