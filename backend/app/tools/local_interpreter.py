"""
本地 Jupyter Code Interpreter
基于 jupyter-client 实现代码执行
"""
import os
import glob
import asyncio
import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Optional

import nbformat
from nbformat import v4 as nbf
from jupyter_client import AsyncKernelManager

from app.tools.base_interpreter import BaseCodeInterpreter
from app.utils.log_util import logger


class LocalCodeInterpreter(BaseCodeInterpreter):
    """本地 Jupyter Kernel 代码解释器"""

    _MATPLOTLIB_FONT_LIST = "['WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'SimHei', 'Microsoft YaHei', 'PingFang SC', 'Arial Unicode MS', 'DejaVu Sans']"
    _MATPLOTLIB_FONT_SETUP = f"""\
try:
    import matplotlib
    from matplotlib import font_manager
    font_manager._load_fontmanager(try_read_cache=False)
    matplotlib.rcParams['font.family'] = 'sans-serif'
    matplotlib.rcParams['font.sans-serif'] = {_MATPLOTLIB_FONT_LIST}
    matplotlib.rcParams['axes.unicode_minus'] = False
except Exception:
    pass
"""

    def __init__(self, work_dir: str, chart_language: str = "English"):
        self.work_dir = work_dir
        self.chart_language = chart_language if chart_language in {"English", "Simplified Chinese"} else "English"
        self.kernel_manager: Optional[AsyncKernelManager] = None
        self.kernel_client = None
        self.notebook = nbf.new_notebook()
        self.current_section = ""
        self._initialized = False

    @staticmethod
    def _kernel_bootstrap_code(work_dir: str, chart_language: str = "English") -> str:
        language_json = json.dumps(chart_language if chart_language in {"English", "Simplified Chinese"} else "English")
        return f"""
import os
import json
os.chdir(r'''{work_dir}''')

try:
    import matplotlib
    from matplotlib import font_manager
    # 清除字体缓存，确保能识别容器中后安装的中文字体
    font_manager._load_fontmanager(try_read_cache=False)
    matplotlib.rcParams['font.family'] = 'sans-serif'
    matplotlib.rcParams['font.sans-serif'] = [
        'WenQuanYi Micro Hei',
        'WenQuanYi Zen Hei',
        'Noto Sans CJK SC',
        'SimHei',
        'Microsoft YaHei',
        'PingFang SC',
        'Arial Unicode MS',
        'DejaVu Sans',
    ]
    matplotlib.rcParams['axes.unicode_minus'] = False
except Exception:
    pass

MCMER_CHART_LANGUAGE = {language_json}
MCMER_FIGURE_MANIFEST = 'figure_artifacts.json'
MCMER_ALLOWED_LATIN_TOKENS = {{
    'cm', 'm', 's', 'kg', 'rad', 'hz', 'db', 'si', 'sic', 'fp', 'fft',
    'r', 'r2', 'aic', 'bic', 'rmse', 'mae', 'cos', 'sin', 'tan', 'log',
    'ln', 'pi', 'nm', 'mm', 'uv', 'ir', 'f', 'p', 'n', 'd'
}}

def _mcmer_figure_language_ok(visible_text_language):
    value = str(visible_text_language or '').strip().lower()
    if MCMER_CHART_LANGUAGE == 'English':
        return value in {{'english', 'en', 'en-us', 'en_gb', 'ascii english'}}
    return value in {{'simplified chinese', 'chinese', 'zh', 'zh-cn', 'zh_cn', '中文', '简体中文', '简体'}}

def _mcmer_collect_figure_text(fig):
    texts = []
    try:
        for text_obj in fig.findobj(lambda obj: hasattr(obj, 'get_text')):
            text = str(text_obj.get_text() or '').strip()
            if text:
                texts.append(text)
    except Exception:
        pass
    return texts

def _mcmer_has_meaningful_latin(text):
    import re
    words = re.findall(r'[A-Za-z]{{2,}}', str(text or ''))
    return any(word.lower() not in MCMER_ALLOWED_LATIN_TOKENS for word in words)

def _mcmer_has_cjk(text):
    import re
    return bool(re.search(r'[\u4e00-\u9fff]', str(text or '')))

def _mcmer_available_font_names():
    try:
        from matplotlib import font_manager
        font_manager._load_fontmanager(try_read_cache=False)
        return {{f.name for f in font_manager.fontManager.ttflist}}
    except Exception:
        return set()

def _mcmer_configure_paper_fonts():
    try:
        import matplotlib
        from matplotlib import font_manager
        font_manager._load_fontmanager(try_read_cache=False)
        available = {{f.name for f in font_manager.fontManager.ttflist}}
        chinese_fonts = [
            'WenQuanYi Micro Hei',
            'WenQuanYi Zen Hei',
            'Noto Sans CJK SC',
            'Noto Sans CJK JP',
            'Source Han Sans SC',
            'SimHei',
            'Microsoft YaHei',
            'PingFang SC',
            'Arial Unicode MS',
        ]
        chosen = [name for name in chinese_fonts if name in available]
        if MCMER_CHART_LANGUAGE == 'Simplified Chinese' and not chosen:
            raise RuntimeError(
                'No CJK font is available in the container. Install WenQuanYi or Noto Sans CJK '
                'before generating Chinese paper figures.'
            )
        matplotlib.rcParams['font.family'] = 'sans-serif'
        matplotlib.rcParams['font.sans-serif'] = chosen + ['DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
        matplotlib.rcParams['mathtext.fontset'] = 'dejavusans'
        return chosen
    except Exception:
        raise

def save_paper_figure(fig, filename, *, visible_text_language=None, visible_text_audit=None, directory='output', dpi=300, **savefig_kwargs):
    \"\"\"Save a final paper figure and register its FigureArtifact metadata.

    Use this helper for every figure that may be cited in the paper. Temporary
    diagnostics should be saved under debug_artifacts and must not be registered
    as paper_ready=True.
    \"\"\"
    from pathlib import Path
    if fig is None:
        try:
            import matplotlib.pyplot as plt
            fig = plt.gcf()
        except Exception as exc:
            raise ValueError('save_paper_figure requires a matplotlib figure') from exc

    directory = str(directory or 'output').strip().replace('\\\\', '/').strip('/')
    if directory not in {{'output', 'figures', 'final_results'}}:
        raise ValueError("paper figures must be saved under output/, figures/, or final_results/")
    target_language = visible_text_language or MCMER_CHART_LANGUAGE
    if not _mcmer_figure_language_ok(target_language):
        raise ValueError(f"visible_text_language={{target_language!r}} does not match required chart language {{MCMER_CHART_LANGUAGE}}")

    _mcmer_configure_paper_fonts()

    collected_text = _mcmer_collect_figure_text(fig)
    audit = visible_text_audit or collected_text
    if isinstance(audit, str):
        audit = [audit]
    if not isinstance(audit, list) or not audit:
        raise ValueError('visible_text_audit must be a non-empty list of actual visible text strings')
    text_audit = [str(item or '').strip() for item in audit if str(item or '').strip()]
    generic_markers = {{'title', 'axis labels', 'legend', 'annotations', 'colorbar', 'tick labels', 'legend/annotations/colorbar/tick labels'}}
    if not text_audit or all(item.lower() in generic_markers for item in text_audit):
        raise ValueError('visible_text_audit must include actual visible text strings, not only generic field names')
    combined_text = '\\n'.join([*collected_text, *text_audit])
    if any(marker in combined_text for marker in ['□', '�', '\\ufffd']):
        raise ValueError('visible chart text contains missing-font/tofu markers; fix fonts or labels before saving')
    if MCMER_CHART_LANGUAGE == 'Simplified Chinese':
        if _mcmer_has_meaningful_latin(combined_text) and not _mcmer_has_cjk(combined_text):
            raise ValueError('Chinese paper figures must not use English-only visible labels')
    elif _mcmer_has_cjk(combined_text):
        raise ValueError('English paper figures must not contain Chinese visible labels')

    filename = str(filename or '').strip().replace('\\\\', '/').lstrip('./')
    if not filename:
        raise ValueError('filename is required')
    if '/' in filename:
        filename = Path(filename).name
    if Path(filename).suffix.lower() not in {{'.png', '.jpg', '.jpeg', '.svg', '.webp'}}:
        filename = filename + '.png'

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    fig.savefig(path, dpi=dpi, bbox_inches='tight', **savefig_kwargs)
    rel_path = path.as_posix()
    artifact = {{
        'path': rel_path,
        'kind': 'figure',
        'paper_ready': True,
        'visible_text_language': MCMER_CHART_LANGUAGE,
        'chart_language_verified': True,
        'visible_text_audit': text_audit,
    }}

    manifest_path = Path(MCMER_FIGURE_MANIFEST)
    try:
        payload = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {{}}
    except Exception:
        payload = {{}}
    figures = payload.get('figures', []) if isinstance(payload, dict) else []
    figures = [item for item in figures if isinstance(item, dict) and item.get('path') != rel_path]
    figures.append(artifact)
    manifest_path.write_text(json.dumps({{'figures': figures}}, ensure_ascii=False, indent=2), encoding='utf-8')
    return artifact
""".strip()

    async def _run_kernel_code(self, code: str, timeout: int = 30) -> tuple[str, bool, str]:
        """在已启动的 kernel 中直接执行代码，避免初始化阶段递归进入 execute_code。"""
        msg_id = self.kernel_client.execute(code)

        outputs: list[str] = []
        error_occurred = False
        error_message = ""

        while True:
          try:
              msg = await asyncio.wait_for(
                  self.kernel_client.get_iopub_msg(), timeout=timeout
              )
          except asyncio.TimeoutError:
              return "\n".join(outputs), True, "代码执行超时"

          if msg.get("parent_header", {}).get("msg_id") != msg_id:
              continue

          msg_type = msg["header"]["msg_type"]
          content = msg["content"]

          if msg_type == "stream":
              outputs.append(content.get("text", ""))
          elif msg_type == "execute_result":
              outputs.append(content.get("data", {}).get("text/plain", ""))
          elif msg_type == "error":
              outputs.append("\n".join(content.get("traceback", [])))
              error_occurred = True
              error_message = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
          elif msg_type == "status" and content.get("execution_state") == "idle":
              break

        return "\n".join(outputs), error_occurred, error_message

    async def _ensure_kernel(self):
        """确保 Kernel 已启动"""
        if self._initialized:
            return

        self.kernel_manager = AsyncKernelManager()
        await self.kernel_manager.start_kernel()
        self.kernel_client = self.kernel_manager.client()
        self.kernel_client.start_channels()
        await self.kernel_client.wait_for_ready()
        self._initialized = True

        # 设置工作目录并补齐 Matplotlib 中文字体配置
        init_result, init_error, init_error_message = await self._run_kernel_code(
            self._kernel_bootstrap_code(self.work_dir, self.chart_language)
        )
        if init_error:
            self._initialized = False
            raise RuntimeError(init_error_message or init_result or "Kernel 初始化失败")
        logger.info("Jupyter Kernel 已启动")

    def add_section(self, title: str) -> None:
        """添加 Markdown 标题段落"""
        self.current_section = title
        self.notebook.cells.append(
            nbf.new_markdown_cell(f"## {title}")
        )

    async def execute(self, code: str, section: str = "") -> str:
        """执行代码（基类接口）"""
        result, _, _ = await self.execute_code(code)
        return result

    @classmethod
    def _normalize_matplotlib_font_settings(cls, code: str) -> str:
        """把模型生成的字体配置重写为容器内可用的中文字体优先级。"""
        matplotlib_tokens = (
            "matplotlib",
            "plt.",
            "seaborn",
            "sns.",
        )
        if not any(token in code for token in matplotlib_tokens):
            return code

        normalized = code
        rcparams_owner = r"((?:plt|matplotlib)\.rcParams)"
        safe_replacements = [
            (
                rf"{rcparams_owner}\[['\"]font\.sans-serif['\"]\]\s*=\s*\[[^\]]*\]",
                lambda match: f"{match.group(1)}['font.sans-serif'] = {cls._MATPLOTLIB_FONT_LIST}",
            ),
            (
                rf"{rcparams_owner}\[['\"]font\.family['\"]\]\s*=\s*[^\n]+",
                lambda match: f"{match.group(1)}['font.family'] = 'sans-serif'",
            ),
            (
                rf"{rcparams_owner}\[['\"]axes\.unicode_minus['\"]\]\s*=\s*[^\n]+",
                lambda match: f"{match.group(1)}['axes.unicode_minus'] = False",
            ),
        ]

        for pattern, replacement in safe_replacements:
            normalized = re.sub(pattern, replacement, normalized)

        font_setup = cls._MATPLOTLIB_FONT_SETUP.strip()
        plot_call = re.search(
            r"^(\s*)(?:[A-Za-z_][\w\s,]*=\s*)?"
            r"(?:plt\.(?:figure|subplots|subplot|plot|scatter|bar|barh|hist)|"
            r"sns\.(?:lineplot|scatterplot|barplot|histplot|heatmap|boxplot|violinplot|regplot|relplot|catplot))\(",
            normalized,
            flags=re.MULTILINE,
        )
        if plot_call and not plot_call.group(1):
            normalized = (
                normalized[:plot_call.start()]
                + font_setup
                + "\n"
                + normalized[plot_call.start():]
            )
        else:
            normalized = font_setup + "\n" + normalized

        return normalized

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """执行代码，返回 (输出文本, 是否出错, 错误信息)
        
        兼容根目录 coder_agent 的调用约定。
        """
        await self._ensure_kernel()

        code = self._normalize_matplotlib_font_settings(code)

        unavailable_imports = self._find_unavailable_imports(code)
        if unavailable_imports:
            modules = ", ".join(unavailable_imports)
            error_message = (
                "检测到未安装的第三方库导入: "
                f"{modules}。请改用当前环境已安装的库，或使用标准库/已存在结果文件完成任务。"
            )
            logger.warning(error_message)
            return error_message, True, error_message

        # 记录执行前的图片文件
        images_before = set(self._list_images())

        # 添加代码单元格
        cell = nbf.new_code_cell(code)
        self.notebook.cells.append(cell)

        try:
            msg_id = self.kernel_client.execute(code)

            outputs = []
            error_occurred = False
            error_message = ""
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self.kernel_client.get_iopub_msg(), timeout=30
                    )
                except asyncio.TimeoutError:
                    outputs.append("[超时] 代码执行超过30秒")
                    return "\n".join(outputs), True, "代码执行超时"

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg["header"]["msg_type"]
                content = msg["content"]

                if msg_type == "stream":
                    text = content.get("text", "")
                    outputs.append(text)
                    cell.outputs.append(
                        nbf.new_output("stream", text=text,
                                       name=content.get("name", "stdout"))
                    )
                elif msg_type == "execute_result":
                    text = content.get("data", {}).get("text/plain", "")
                    outputs.append(text)
                    cell.outputs.append(
                        nbf.new_output("execute_result", data=content.get("data", {}))
                    )
                elif msg_type == "display_data":
                    cell.outputs.append(
                        nbf.new_output("display_data", data=content.get("data", {}))
                    )
                    if "image/png" in content.get("data", {}):
                        img_data = content["data"]["image/png"]
                        outputs.append("[图片已生成]")
                elif msg_type == "error":
                    traceback = "\n".join(content.get("traceback", []))
                    outputs.append(traceback)
                    error_occurred = True
                    error_message = f"{content.get('ename', 'Error')}: {content.get('evalue', '')}"
                    cell.outputs.append(
                        nbf.new_output("error", ename=content.get("ename", ""),
                                       evalue=content.get("evalue", ""),
                                       traceback=content.get("traceback", []))
                    )
                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break

            result_text = "\n".join(outputs) if outputs else "代码执行完成 (无输出)"
            return result_text, error_occurred, error_message

        except Exception as e:
            error_msg = f"代码执行异常: {str(e)}"
            logger.error(error_msg)
            return error_msg, True, error_msg

    def _list_images(self) -> list[str]:
        """列出 work_dir 中可交付图片文件，返回相对路径。"""
        root = Path(self.work_dir)
        if not root.exists():
            return []
        suffixes = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".webp"}
        ignored_parts = {"debug_artifacts", "inputs", "data", "source_bundle", "results"}
        allowed_top_level_dirs = {"output", "figures", "final_results"}
        images: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            rel = path.relative_to(root)
            if any(part in ignored_parts for part in rel.parts):
                continue
            if not rel.parts or rel.parts[0] not in allowed_top_level_dirs:
                continue
            images.append(rel.as_posix())
        return images

    @staticmethod
    def _find_unavailable_imports(code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".", 1)[0]
                    if top_level:
                        imports.add(top_level)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    top_level = node.module.split(".", 1)[0]
                    if top_level:
                        imports.add(top_level)

        unavailable = []
        for module_name in sorted(imports):
            if importlib.util.find_spec(module_name) is None:
                unavailable.append(module_name)
        return unavailable

    async def get_created_images(self, section: str = "") -> list[str]:
        """返回当前 work_dir 中的图片文件名列表"""
        return self._list_images()

    async def save_notebook(self, filepath: str) -> None:
        """保存 notebook"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            nbformat.write(self.notebook, f)
        logger.info(f"Notebook 已保存: {filepath}")

    async def close(self) -> None:
        """关闭 Kernel"""
        if self.kernel_manager:
            try:
                await self.kernel_manager.shutdown_kernel()
            except Exception:
                pass
            self._initialized = False
            logger.info("Jupyter Kernel 已关闭")
