"""document export responsibilities."""

from __future__ import annotations

import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from app.utils.markdown import enforce_markdown_image_whitelist, normalize_math_markdown


def _resolve_markdown_image_path(md_path: str, image_target: str) -> Path | None:
    target = (image_target or "").strip().strip("<>").strip()
    if not target or re.match(r"^[a-zA-Z]+://", target):
        return None

    if " \"" in target:
        target = target.split(' "', 1)[0].strip()

    md_dir = Path(md_path).parent
    raw_candidate = Path(target)

    search_candidates: list[Path] = []
    if raw_candidate.is_absolute():
        search_candidates.append(raw_candidate)
    else:
        search_candidates.extend(
            [
                md_dir / raw_candidate,
                md_dir / raw_candidate.name,
                md_dir / "output" / raw_candidate.name,
                md_dir / "final_results" / raw_candidate.name,
                md_dir / "debug_artifacts" / raw_candidate.name,
                md_dir / "charts" / raw_candidate.name,
                md_dir / "images" / raw_candidate.name,
                md_dir / "figures" / raw_candidate.name,
            ]
        )

    for candidate in search_candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate.absolute()
        if resolved.exists() and resolved.is_file():
            return resolved

    if not raw_candidate.is_absolute() and raw_candidate.name:
        for folder_name in ["output", "final_results", "debug_artifacts", "charts", "images", "figures"]:
            folder = md_dir / folder_name
            if not folder.exists() or not folder.is_dir():
                continue
            matches = list(folder.rglob(raw_candidate.name))
            if matches:
                return matches[0]

    return None


@lru_cache(maxsize=1)
def _get_mml2omml_transform() -> Any | None:
    """Load the Office MathML-to-OMML XSLT when available."""
    try:
        from lxml import etree
    except Exception:
        return None

    candidates = [
        os.environ.get("MML2OMML_XSL_PATH", "").strip(),
        r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\MML2OMML.XSL",
        r"C:\Program Files\Microsoft Office\Office16\MML2OMML.XSL",
        r"C:\Program Files (x86)\Microsoft Office\Office16\MML2OMML.XSL",
    ]
    stylesheet_path = next((item for item in candidates if item and os.path.exists(item)), "")
    if not stylesheet_path:
        return None

    try:
        return etree.XSLT(etree.parse(stylesheet_path))
    except Exception:
        return None


def _latex_to_omml_element(latex: str, display: bool = False) -> Any | None:
    """Convert a LaTeX formula into an OMML XML element for python-docx."""
    transform = _get_mml2omml_transform()
    if transform is None:
        return None

    try:
        from latex2mathml.converter import convert
        from lxml import etree
    except Exception:
        return None

    expression = (latex or "").strip()
    if not expression:
        return None

    try:
        mathml = convert(expression)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
        mathml_root = etree.fromstring(mathml.encode("utf-8"), parser=parser)
        mathml_root.set("display", "block" if display else "inline")
        omml_tree = transform(mathml_root)
        return omml_tree.getroot()
    except Exception:
        return None


def _append_omml(paragraph: Any, latex: str, display: bool = False) -> bool:
    omml_element = _latex_to_omml_element(latex, display=display)
    if omml_element is None:
        return False
    paragraph._element.append(omml_element)
    return True


def _iter_inline_math_segments(text: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    buffer: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            buffer.append(text[index:index + 2])
            index += 2
            continue

        if char != "$" or (index + 1 < length and text[index + 1] == "$"):
            buffer.append(char)
            index += 1
            continue

        end = index + 1
        while end < length:
            if text[end] == "\\" and end + 1 < length:
                end += 2
                continue
            if text[end] == "$" and not (end + 1 < length and text[end + 1] == "$"):
                break
            end += 1

        if end >= length:
            buffer.append(char)
            index += 1
            continue

        if buffer:
            segments.append(("text", "".join(buffer)))
            buffer = []
        segments.append(("math", text[index + 1:end]))
        index = end + 1

    if buffer:
        segments.append(("text", "".join(buffer)))
    return segments


def _add_text_with_inline_math(paragraph: Any, text: str) -> None:
    for segment_type, value in _iter_inline_math_segments(text):
        if segment_type == "text":
            if value:
                paragraph.add_run(value.replace(r"\$", "$"))
            continue
        if not _append_omml(paragraph, value, display=False):
            paragraph.add_run(f"${value}$")


def md_to_docx(md_path: str, docx_path: str) -> bool:
    """尝试将 markdown 转为 docx，优先保留 LaTeX 公式为 Word 数学对象。"""
    temp_md_path: Optional[str] = None

    try:
        normalized_markdown = normalize_math_markdown(Path(md_path).read_text(encoding="utf-8"))
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as tmp:
            tmp.write(normalized_markdown)
            temp_md_path = tmp.name
    except Exception:
        temp_md_path = None

    try:
        import pypandoc

        pypandoc.convert_file(
            temp_md_path or md_path,
            "docx",
            outputfile=docx_path,
            extra_args=[
                "--resource-path="
                + os.pathsep.join(
                    str(Path(md_path).parent / folder)
                    for folder in ["", "output", "figures", "final_results", "charts", "images"]
                )
            ],
        )
        # pypandoc 在 outputfile 模式下通常返回空字符串
        return os.path.exists(docx_path)
    except Exception:
        pass
    finally:
        if temp_md_path and os.path.exists(temp_md_path):
            try:
                os.remove(temp_md_path)
            except OSError:
                pass

    try:
        from docx import Document
        from docx.shared import Inches
        from docx.shared import Pt
    except Exception:
        return False

    try:
        markdown = normalized_markdown if 'normalized_markdown' in locals() else normalize_math_markdown(Path(md_path).read_text(encoding="utf-8"))
    except Exception:
        return False

    document = Document()
    normal_style = document.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(11)

    lines = markdown.splitlines()
    in_code_block = False
    code_buffer: list[str] = []
    table_buffer: list[str] = []

    def flush_code() -> None:
        nonlocal code_buffer
        if not code_buffer:
            return
        paragraph = document.add_paragraph()
        run = paragraph.add_run("\n".join(code_buffer))
        run.font.name = "Consolas"
        run.font.size = Pt(10)
        code_buffer = []

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = []
        for raw in table_buffer:
            stripped = raw.strip()
            if not stripped:
                continue
            if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", stripped):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows.append(cells)
        if rows:
            column_count = max(len(row) for row in rows)
            table = document.add_table(rows=len(rows), cols=column_count)
            table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                for col_index, cell in enumerate(row):
                    table.cell(row_index, col_index).text = cell
        table_buffer = []

    line_index = 0
    while line_index < len(lines):
        stripped = lines[line_index].rstrip()
        if stripped.startswith("```"):
            flush_table()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
            line_index += 1
            continue

        if in_code_block:
            code_buffer.append(stripped)
            line_index += 1
            continue

        if "|" in stripped and stripped.count("|") >= 2:
            table_buffer.append(stripped)
            line_index += 1
            continue

        flush_table()

        if not stripped.strip():
            document.add_paragraph("")
            line_index += 1
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = min(len(heading_match.group(1)), 6)
            document.add_heading(heading_match.group(2).strip(), level=level)
            line_index += 1
            continue

        bullet_match = re.match(r"^\s*[-*+]\s+(.*)$", stripped)
        if bullet_match:
            paragraph = document.add_paragraph(style="List Bullet")
            _add_text_with_inline_math(paragraph, bullet_match.group(1).strip())
            line_index += 1
            continue

        ordered_match = re.match(r"^\s*\d+[.)]\s+(.*)$", stripped)
        if ordered_match:
            paragraph = document.add_paragraph(style="List Number")
            _add_text_with_inline_math(paragraph, ordered_match.group(1).strip())
            line_index += 1
            continue

        image_match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped.strip())
        if image_match:
            alt_text = image_match.group(1).strip() or "图片"
            path_text = image_match.group(2).strip()
            image_path = _resolve_markdown_image_path(md_path, path_text)
            if image_path:
                picture_paragraph = document.add_paragraph()
                picture_paragraph.alignment = 1
                try:
                    picture_paragraph.add_run().add_picture(str(image_path), width=Inches(6))
                except Exception:
                    picture_paragraph.add_run().add_picture(str(image_path))

                caption = document.add_paragraph(alt_text)
                caption.alignment = 1
            else:
                document.add_paragraph(f"[图片缺失] {alt_text}: {path_text}")
            line_index += 1
            continue

        if stripped.strip() == "$$":
            block_lines: list[str] = []
            line_index += 1
            while line_index < len(lines) and lines[line_index].strip() != "$$":
                block_lines.append(lines[line_index])
                line_index += 1
            paragraph = document.add_paragraph()
            if not _append_omml(paragraph, "\n".join(block_lines), display=True):
                paragraph.add_run("$$\n" + "\n".join(block_lines) + "\n$$")
            line_index += 1
            continue

        paragraph = document.add_paragraph()
        _add_text_with_inline_math(paragraph, stripped)
        line_index += 1

    flush_table()
    flush_code()

    try:
        document.save(docx_path)
    except Exception:
        return False
    return os.path.exists(docx_path)


def finalize_markdown_export(
    paper_path: str,
    paper_content: str,
    docx_path: str,
    allowed_images: list[str] | None = None,
) -> bool:
    """Write normalized Markdown and export DOCX through the shared image gate."""
    normalized_paper = normalize_math_markdown(paper_content)
    normalized_paper = enforce_markdown_image_whitelist(normalized_paper, allowed_images)
    with open(paper_path, "w", encoding="utf-8") as f:
        f.write(normalized_paper)
    return md_to_docx(paper_path, docx_path)
