"""coder chart language responsibilities."""

from __future__ import annotations

import re


def _chart_language_from_prompt(prompt: str) -> str:
    if (
        "document/problem language is Simplified Chinese" in prompt
        or "visible_text_language\":\"Simplified Chinese" in prompt
        or '"chart_language": "Simplified Chinese"' in prompt
        or '"document_language": "Simplified Chinese"' in prompt
    ):
        return "Simplified Chinese"
    if (
        "document/problem language is English" in prompt
        or "visible_text_language\":\"English" in prompt
        or '"chart_language": "English"' in prompt
        or '"document_language": "English"' in prompt
    ):
        return "English"
    return ""


def _english_chart_label_literals(code: str) -> list[str]:
    label_patterns = [
        r"(?:plt\.)?(?:title|xlabel|ylabel|suptitle)\(\s*f?[\"']([^\"']*[A-Za-z][^\"']*)[\"']",
        r"\.(?:set_title|set_xlabel|set_ylabel)\(\s*f?[\"']([^\"']*[A-Za-z][^\"']*)[\"']",
        r"\blabel\s*=\s*f?[\"']([^\"']*[A-Za-z][^\"']*)[\"']",
    ]
    allowed_fragments = {
        "cm", "m", "s", "kg", "rad", "hz", "db", "rgb", "cmyk", "fft", "fp", "sic",
        "si", "r", "r2", "aic", "bic", "rmse", "mae",
    }
    labels: list[str] = []
    for pattern in label_patterns:
        for match in re.finditer(pattern, code, flags=re.IGNORECASE):
            text = match.group(1).strip()
            words = re.findall(r"[A-Za-z]{2,}", text)
            meaningful = [word for word in words if word.lower() not in allowed_fragments]
            if meaningful:
                labels.append(text)
    return labels


def _chinese_chart_label_literals(code: str) -> list[str]:
    label_patterns = [
        r"(?:plt\.)?(?:title|xlabel|ylabel|suptitle)\(\s*f?[\"']([^\"']*[\u4e00-\u9fff][^\"']*)[\"']",
        r"\.(?:set_title|set_xlabel|set_ylabel)\(\s*f?[\"']([^\"']*[\u4e00-\u9fff][^\"']*)[\"']",
        r"\blabel\s*=\s*f?[\"']([^\"']*[\u4e00-\u9fff][^\"']*)[\"']",
    ]
    labels: list[str] = []
    for pattern in label_patterns:
        for match in re.finditer(pattern, code, flags=re.IGNORECASE):
            labels.append(match.group(1).strip())
    return labels


def _chart_language_guard_message(figure_mode: str, code: str, expected_chart_language: str) -> str:
    # --- Helper exploration blocking (all modes) ---
    if re.search(r"\bdef\s+save_paper_figure\b|\bsave_paper_figure\s*=", code):
        return (
            "FigureArtifact guard blocked this execute_code call: save_paper_figure is a protected "
            "kernel helper and must not be redefined, shadowed, or monkey-patched. Use the injected helper directly."
        )
    if re.search(r"inspect\.signature|inspect\.getsource|pkgutil\.iter_modules|importlib\.import_module.*mcmer", code):
        return (
            "Guard blocked: do not inspect or explore helper modules. save_paper_figure is already "
            "injected into the kernel — call it directly when needed. Focus on computation, not environment exploration."
        )

    # --- figure_mode=none: block ALL figure code ---
    if figure_mode == "none":
        figure_signals = re.findall(
            r"savefig|save_paper_figure|pyplot|plt\.show|fig\.savefig|\.savefig|imshow|plt\.",
            code, flags=re.IGNORECASE,
        )
        if figure_signals:
            return (
                "当前子任务是纯数值任务 (figure_mode=none)，禁止生成任何图表。"
                "请删除所有绘图/保存图像代码，只输出数值和结构化结果。"
                "不要 import matplotlib.pyplot，不要调用 savefig/save_paper_figure。"
            )
        return ""

    savefig_targets = [
        match.group(1).replace("\\", "/").lstrip("./")
        for match in re.finditer(
            r"(?:\.savefig|savefig)\(\s*f?[\"']([^\"']+\.(?:png|jpg|jpeg|svg|webp))[\"']",
            code,
            flags=re.IGNORECASE,
        )
    ]
    writes_deliverable_image = any(
        not target.startswith("debug_artifacts/")
        for target in savefig_targets
    )
    savefig_calls = re.findall(r"(?:[\w.]+\.)?savefig\s*\(", code, flags=re.IGNORECASE)
    has_unparsed_savefig = bool(savefig_calls) and len(savefig_targets) < len(savefig_calls)
    uses_paper_figure_helper = "save_paper_figure(" in code
    if not writes_deliverable_image and not uses_paper_figure_helper:
        if has_unparsed_savefig:
            return (
                "FigureArtifact guard blocked this execute_code call: savefig calls with variable or "
                "non-literal targets are not allowed. Use save_paper_figure for final figures, or save "
                "diagnostics with an explicit debug_artifacts/... literal path."
            )
        return ""
    if writes_deliverable_image or has_unparsed_savefig:
        return (
            "FigureArtifact guard blocked this execute_code call: final paper images must be saved "
            "with save_paper_figure(fig, filename, visible_text_audit=[...], linked_result_ids=[...], source_data=[...]) so language metadata is registered. "
            "Use plt.savefig/fig.savefig only for diagnostics under debug_artifacts/."
        )
    # --- Pre-validate save_paper_figure() call arguments (AST-based) ---
    if uses_paper_figure_helper:
        import ast as _ast
        from app.utils.figure_artifacts import is_placeholder_filename as _is_ph, contains_placeholder_text as _has_ph
        try:
            tree = _ast.parse(code)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                # Match save_paper_figure(...) calls
                func = node.func
                if isinstance(func, _ast.Name) and func.id == "save_paper_figure":
                    pass
                elif isinstance(func, _ast.Attribute) and func.attr == "save_paper_figure":
                    pass
                else:
                    continue

                # Extract keyword arguments
                kw_map = {kw.arg: kw.value for kw in node.keywords}

                # --- filename (2nd positional arg) ---
                fname = None
                if len(node.args) >= 2:
                    a = node.args[1]
                    if isinstance(a, _ast.Constant) and isinstance(a.value, str):
                        fname = a.value
                if fname and _is_ph(fname):
                    return (
                        f"FigureArtifact guard blocked: filename '{fname}' matches placeholder/test pattern. "
                        "Use a descriptive name like problem1_trajectory.png."
                    )

                # --- Helper: check that a kwarg is a literal non-empty list ---
                def _require_literal_list(node: object, param_name: str) -> str | None:
                    if not isinstance(node, _ast.List):
                        return (
                            f"FigureArtifact guard blocked: '{param_name}' must be a literal list, not a variable or expression. "
                            "Inline the values directly so the guard can validate them."
                        )
                    if len(node.elts) == 0:
                        return (
                            f"FigureArtifact guard blocked: {param_name}=[] is empty. "
                            "Must contain at least one entry."
                        )
                    return None

                # --- linked_result_ids ---
                if "linked_result_ids" not in kw_map:
                    return (
                        "FigureArtifact guard blocked: save_paper_figure() call is missing required 'linked_result_ids' parameter. "
                        "Add linked_result_ids=['result_id'] referencing verified results."
                    )
                err = _require_literal_list(kw_map["linked_result_ids"], "linked_result_ids")
                if err:
                    return err

                # --- source_data ---
                if "source_data" not in kw_map:
                    return (
                        "FigureArtifact guard blocked: save_paper_figure() call is missing required 'source_data' parameter. "
                        "Add source_data=['result_registry.json'] listing data sources."
                    )
                err = _require_literal_list(kw_map["source_data"], "source_data")
                if err:
                    return err

                # --- visible_text_audit ---
                if "visible_text_audit" in kw_map:
                    va_node = kw_map["visible_text_audit"]
                    if not isinstance(va_node, _ast.List):
                        return (
                            "FigureArtifact guard blocked: 'visible_text_audit' must be a literal list, not a variable or expression. "
                            "Inline the actual visible text strings directly."
                        )
                    if len(va_node.elts) < 2:
                        return (
                            "FigureArtifact guard blocked: visible_text_audit must contain at least 2 real visible text strings "
                            "(e.g. ['波长/nm', '反射率/%']), not just one generic field name."
                        )
                    # Check if all elements are generic markers
                    _generic_markers = {"title", "axis labels", "legend", "annotations", "colorbar", "tick labels"}
                    vals = []
                    for elt in va_node.elts:
                        if isinstance(elt, _ast.Constant) and isinstance(elt.value, str):
                            vals.append(elt.value.strip().lower())
                    if vals and all(v in _generic_markers for v in vals):
                        return (
                            "FigureArtifact guard blocked: visible_text_audit contains only generic field names "
                            f"({vals}). Must contain actual visible text strings like ['波长/nm', '反射率/%']."
                        )
    if expected_chart_language == "Simplified Chinese":
        english_labels = _english_chart_label_literals(code)
        if english_labels:
            preview = "; ".join(english_labels[:8])
            return (
                "Chart language guard blocked this execute_code call: the task requires Simplified Chinese "
                "visible chart text, but deliverable image code contains English chart labels: "
                f"{preview}. Rewrite titles, axis labels, legends, annotations and tick/category labels in Chinese."
            )
    if expected_chart_language == "English":
        chinese_labels = _chinese_chart_label_literals(code)
        if chinese_labels:
            preview = "; ".join(chinese_labels[:8])
            return (
                "Chart language guard blocked this execute_code call: the task requires English "
                "visible chart text, but deliverable image code contains Chinese chart labels: "
                f"{preview}. Rewrite titles, axis labels, legends, annotations and tick/category labels in English."
            )
    return ""
