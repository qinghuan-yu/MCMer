"""
通用工具函数
"""
import os
import json
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

        output = pypandoc.convert_file(md_path, "docx", outputfile=docx_path)
        # pypandoc 在 outputfile 模式下通常返回空字符串
        return os.path.exists(docx_path)
    except Exception:
        return False
