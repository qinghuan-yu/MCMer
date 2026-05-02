"""
Prompt 模板管理
"""
import tomli
from pathlib import Path
from typing import Optional

from app.schemas.enums import FormatOutPut


# 默认 Prompt 模板路径
TEMPLATE_PATH = Path(__file__).parent.parent / "config" / "md_template.toml"


def _load_template() -> dict:
    """加载 TOML 模板"""
    try:
        with open(TEMPLATE_PATH, "rb") as f:
            return tomli.load(f)
    except Exception:
        return {}


_templates = _load_template()


def get_prompt(section: str, key: str, default: str = "") -> str:
    """获取指定模板"""
    try:
        return _templates.get(section, {}).get(key, default).strip()
    except Exception:
        return default


# ============================================
# Agent 系统提示词
# ============================================

COORDINATOR_PROMPT = get_prompt(
    "coordinator", "system_prompt",
    """你是一位资深的数学建模竞赛专家。请分析题目并输出JSON格式的子问题列表。"""
)

MODELER_PROMPT = get_prompt(
    "modeler", "system_prompt",
    """你是一位数学建模专家。请为每个子问题设计建模方案并输出JSON。"""
)

CODER_PROMPT = get_prompt(
    "coder", "system_prompt",
    """你是一位Python编程专家。请根据建模方案编写并执行代码。"""
)


def get_writer_prompt(format_output: FormatOutPut = FormatOutPut.Markdown) -> str:
    """获取写作 Agent 的系统提示词"""
    if format_output == FormatOutPut.Latex:
        return get_prompt(
            "writer", "system_prompt_latex",
            """你是一位学术论文写作专家，使用LaTeX撰写论文。"""
        )
    return get_prompt(
        "writer", "system_prompt_md",
        """你是一位学术论文写作专家，使用Markdown撰写论文。"""
    )


def get_reflection_prompt(error_message: str) -> str:
    """获取反思提示词"""
    return f"""你的上一次代码执行发生了错误。请仔细分析以下错误信息并进行修正：

错误信息：
{error_message}

请：
1. 分析错误原因
2. 提供修正后的完整代码
3. 确保修正后的代码可以正确运行"""
