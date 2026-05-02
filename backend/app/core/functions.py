"""
函数/工具定义 (用于 LLM function calling)
"""
import json

# ============================================
# 代码手工具
# ============================================

EXECUTE_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": "在 Jupyter Notebook 中执行 Python 代码，返回执行结果和输出",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码",
                },
                "description": {
                    "type": "string",
                    "description": "对这段代码功能的简要描述",
                },
            },
            "required": ["code"],
        },
    },
}

SAVE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "save_file",
        "description": "将内容保存为文件到工作目录",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "文件名 (如 result.csv, figure.png)",
                },
                "content": {
                    "type": "string",
                    "description": "要保存的内容",
                },
            },
            "required": ["filename", "content"],
        },
    },
}

coder_tools = [EXECUTE_CODE_TOOL, SAVE_FILE_TOOL]

# ============================================
# 写作手工具
# ============================================

SEARCH_PAPERS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_papers",
        "description": "在学术数据库中搜索相关论文，获取引用信息",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数 (默认5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

writer_tools = [SEARCH_PAPERS_TOOL]
