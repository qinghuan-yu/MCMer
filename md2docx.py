from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pypandoc


logger = logging.getLogger(__name__)


def md_2_docx(work_dir: str | Path | None = None) -> Path:
    base_dir = Path(work_dir) if work_dir else Path(__file__).resolve().parent
    md_path = base_dir / "res.md"
    docx_path = base_dir / "res.docx"

    if not md_path.exists():
        raise FileNotFoundError(f"未找到 Markdown 文件: {md_path}")

    extra_args = [
        "--resource-path",
        str(base_dir),
        "--mathml",
        "--standalone",
    ]

    pypandoc.convert_file(
        source_file=str(md_path),
        to="docx",
        outputfile=str(docx_path),
        format="markdown+tex_math_dollars",
        extra_args=extra_args,
    )
    print(f"转换完成: {docx_path}")
    logger.info("转换完成: %s", docx_path)
    return docx_path


def main() -> None:
    parser = argparse.ArgumentParser(description="将当前工作目录中的 res.md 转换为 res.docx")
    parser.add_argument(
        "--work-dir",
        default=Path(__file__).resolve().parent,
        help="Markdown 文件所在目录，默认使用脚本所在目录",
    )
    args = parser.parse_args()
    md_2_docx(args.work_dir)


if __name__ == "__main__":
    main()
