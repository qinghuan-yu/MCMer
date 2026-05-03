"""
通用工具函数
"""
import os
import json
import re
from pathlib import Path
from typing import Optional


def get_current_files(work_dir: str, sub_dir: str = "") -> str:
    """获取当前工作目录下的文件列表"""
    path = Path(work_dir)
    if sub_dir:
        path = path / sub_dir

    if not path.exists():
        return "目录不存在"

    files = []
    for f in sorted(path.iterdir()):
        if f.is_file():
            size_kb = f.stat().st_size / 1024
            files.append(f"- {f.name} ({size_kb:.1f} KB)")

    if not files:
        return "目录为空"
    return "\n".join(files)


def read_file_content(filepath: str) -> Optional[str]:
    """读取文件内容"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def save_json(data: dict, filepath: str) -> None:
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(filepath: str) -> Optional[dict]:
    """加载 JSON 文件"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def md_to_docx(md_path: str, docx_path: str) -> bool:
    """尝试将 markdown 转为 docx，失败时返回 False。"""
    try:
        import pypandoc

        pypandoc.convert_file(md_path, "docx", outputfile=docx_path)
        # pypandoc 在 outputfile 模式下通常返回空字符串
        return os.path.exists(docx_path)
    except Exception:
        pass

    try:
        from docx import Document
        from docx.shared import Pt
    except Exception:
        return False

    try:
        markdown = Path(md_path).read_text(encoding="utf-8")
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

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            flush_table()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_buffer.append(stripped)
            continue

        if "|" in stripped and stripped.count("|") >= 2:
            table_buffer.append(stripped)
            continue

        flush_table()

        if not stripped.strip():
            document.add_paragraph("")
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = min(len(heading_match.group(1)), 6)
            document.add_heading(heading_match.group(2).strip(), level=level)
            continue

        bullet_match = re.match(r"^\s*[-*+]\s+(.*)$", stripped)
        if bullet_match:
            document.add_paragraph(bullet_match.group(1).strip(), style="List Bullet")
            continue

        ordered_match = re.match(r"^\s*\d+[.)]\s+(.*)$", stripped)
        if ordered_match:
            document.add_paragraph(ordered_match.group(1).strip(), style="List Number")
            continue

        image_match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped.strip())
        if image_match:
            alt_text = image_match.group(1).strip() or "图片"
            path_text = image_match.group(2).strip()
            document.add_paragraph(f"[图片] {alt_text}: {path_text}")
            continue

        document.add_paragraph(stripped)

    flush_table()
    flush_code()

    try:
        document.save(docx_path)
    except Exception:
        return False
    return os.path.exists(docx_path)
