"""
代码手 Agent - 执行代码、纠错、生成可视化
"""
from app.core.agents.agent import Agent
from app.config.setting import settings
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, InterpreterMessage
from app.tools.base_interpreter import BaseCodeInterpreter
from app.core.llm.llm import LLM
from app.schemas.A2A import CoderToWriter
from app.core.prompts import CODER_PROMPT, get_reflection_prompt
from app.utils.common_utils import get_current_files
from pathlib import Path
import csv
import importlib.metadata
import json
import re
import time
from app.core.functions import coder_tools


class CoderAgent(Agent):
    def __init__(
        self,
        task_id: str,
        model: LLM,
        work_dir: str,
        max_chat_turns: int = settings.MAX_CHAT_TURNS,
        max_retries: int = settings.MAX_RETRIES,
        max_total_tool_calls: int = settings.CODER_MAX_TOTAL_TOOL_CALLS,
        max_wall_seconds: int = settings.CODER_MAX_WALL_SECONDS,
        code_interpreter: BaseCodeInterpreter = None,
    ) -> None:
        super().__init__(task_id, model, max_chat_turns)
        self.work_dir = work_dir
        self.max_retries = max_retries
        self.max_total_tool_calls = max_total_tool_calls
        self.max_wall_seconds = max_wall_seconds
        self.is_first_run = True
        self.system_prompt = CODER_PROMPT
        self.code_interpreter = code_interpreter
        self.last_executed_code = ""
        self.same_code_repeat_count = 0

    @staticmethod
    def _sanitize_section_name(section: str) -> str:
        sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", (section or "result").strip())
        sanitized = sanitized.strip("_")
        return sanitized or "result"

    def _structured_result_filename(self, subtask_title: str) -> str:
        return f"{self._sanitize_section_name(subtask_title)}_structured_results.json"

    @staticmethod
    def _dedupe_jsonish_items(items: list[object]) -> list[object]:
        deduped: list[object] = []
        seen: set[str] = set()
        for item in items:
            if item is None or item == "":
                continue
            try:
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                marker = str(item)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped

    @staticmethod
    def _is_number_like(value: object) -> bool:
        text = str(value or "").strip().replace(",", "")
        if not text:
            return False
        try:
            float(text)
        except ValueError:
            return False
        return True

    @classmethod
    def _salvage_existing_payload_key_results(
        cls,
        payload: object,
        source_name: str,
        subtask_title: str,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []

        def walk(node: object, path_parts: list[str]) -> None:
            if len(results) >= limit:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, [*path_parts, str(key)])
                return
            if isinstance(node, list):
                for index, value in enumerate(node, start=1):
                    walk(value, [*path_parts, str(index)])
                return
            if not cls._is_number_like(node):
                return

            readable_path = " / ".join(part for part in path_parts if part)
            if not readable_path:
                readable_path = subtask_title
            path_marker = readable_path.lower()
            metadata_markers = [
                "metadata",
                "result_registry_summary",
                "blocked_count",
                "verified_count",
                "warning_count",
                "info_count",
                "expected_but_missing",
                "generated_at",
            ]
            if any(marker in path_marker for marker in metadata_markers):
                return
            result_markers = [
                "关键结果",
                "结果",
                "统计",
                "拟合",
                "误差",
                "参数",
                "指标",
                "预测",
                "校正",
                "阶段",
                "异常",
                "阈值",
                "rmse",
                "mae",
                "r2",
                "r²",
                "metric",
                "score",
            ]
            if not any(marker in path_marker for marker in result_markers):
                return
            value_text = str(node).strip()
            result_id = cls._sanitize_section_name(f"{source_name}_{readable_path}")
            results.append(
                {
                    "id": result_id,
                    "name": readable_path,
                    "value": value_text,
                    "unit": "",
                    "formula": "",
                    "inputs": {},
                    "source_data": [source_name],
                    "code_cell": "",
                    "evidence": f"从阻断前已写入的结构化结果 {source_name} 中自动保全数值：{readable_path} = {value_text}",
                    "source": "existing_structured_payload_salvage",
                    "verified": False,
                    "status": "unverified",
                    "warnings": ["salvaged_from_interrupted_payload_requires_formula_and_unit_verification"],
                }
            )

        walk(payload, [])
        return results

    @classmethod
    def _csv_summary_result(
        cls,
        csv_path: Path,
        rows: list[dict[str, str]],
        subtask_title: str,
        max_preview_rows: int = 8,
    ) -> dict[str, object] | None:
        if not rows:
            return None

        columns = list(rows[0].keys())
        preview_lines = [" | ".join(columns)]
        preview_lines.append(" | ".join("---" for _ in columns))
        for row in rows[:max_preview_rows]:
            preview_lines.append(" | ".join(str(row.get(column, "")).strip() for column in columns))

        result_id = cls._sanitize_section_name(f"{csv_path.stem}_result_table")
        return {
            "id": result_id,
            "name": f"{csv_path.stem} 结果表",
            "value": "\n".join(preview_lines),
            "unit": "",
            "formula": "",
            "inputs": {"row_count": len(rows), "columns": columns},
            "source_data": [csv_path.name],
            "code_cell": "",
            "evidence": (
                f"从已生成结果文件 {csv_path.name} 自动保全表格摘要，共 {len(rows)} 行；"
                f"预览前 {min(len(rows), max_preview_rows)} 行，供论文写作引用完整结果来源。"
            ),
            "source": "artifact_salvage_summary",
            "verified": False,
            "status": "unverified",
            "warnings": ["artifact_table_summary_requires_formula_and_unit_verification"],
        }

    @classmethod
    def _salvage_artifact_key_results(cls, work_dir: str, subtask_title: str, limit: int = 40) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        root = Path(work_dir)
        if not root.exists():
            return results

        for csv_path in sorted(root.glob("*.csv")):
            if len(results) >= limit:
                break
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = [dict(row) for row in reader]
                summary = cls._csv_summary_result(csv_path, rows, subtask_title)
                if summary:
                    results.append(summary)

                if len(results) >= limit:
                    break

                columns = list(rows[0].keys()) if rows else []
                parameter_column = next((column for column in columns if "参数" in column or column.lower() in {"param", "parameter"}), "")
                value_columns = [
                    column
                    for column in columns
                    if column != parameter_column and ("值" in column or "value" in column.lower() or "参数" in column)
                ]
                is_parameter_table = bool(parameter_column and value_columns)
                if not is_parameter_table:
                    continue

                for row_index, row in enumerate(rows, start=1):
                    if len(results) >= limit:
                        break
                    parameter_name = str(row.get(parameter_column, "") or f"row{row_index}").strip()
                    for column in value_columns:
                        if len(results) >= limit:
                            break
                        value_text = str(row.get(column) or "").strip()
                        if not cls._is_number_like(value_text):
                            continue
                        result_id = cls._sanitize_section_name(f"{csv_path.stem}_{parameter_name}_{column}")
                        display_name = f"{parameter_name}（{column}）" if parameter_name else str(column or result_id)
                        results.append(
                            {
                                "id": result_id,
                                "name": display_name,
                                "value": value_text,
                                "unit": "",
                                "formula": "",
                                "inputs": {},
                                "source_data": [csv_path.name],
                                "code_cell": "",
                                "evidence": f"从已生成参数文件 {csv_path.name} 第 {row_index} 行自动保全，避免工具预算阻断后丢失已计算参数。",
                                "source": "artifact_salvage",
                                "verified": False,
                                "status": "unverified",
                                "warnings": ["artifact_scalar_salvage_requires_formula_and_unit_verification"],
                            }
                        )
            except Exception as exc:
                logger.warning("自动保全 CSV 结果失败 %s: %s", csv_path, exc)
        return results

    @staticmethod
    def _list_existing_artifacts(work_dir: str) -> list[str]:
        root = Path(work_dir)
        if not root.exists():
            return []
        suffixes = {".csv", ".json", ".txt", ".md", ".png", ".jpg", ".jpeg", ".svg"}
        ignored = {"task_info.json", "problem_facts.json", "solve_spec.json", "verify_plan.json", "result_registry.json"}
        return sorted(
            path.name
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in suffixes and path.name not in ignored
        )

    @staticmethod
    def _looks_like_non_mutating_summary_code(code: str) -> bool:
        normalized = (code or "").lower()
        if not normalized.strip():
            return False

        summary_markers = [
            "最终总结报告",
            "检查是否还有其他需要修复的问题",
            "所有检查通过",
            "任务已完成",
            "print(",
        ]
        has_summary_intent = any(marker.lower() in normalized for marker in summary_markers)
        if not has_summary_intent:
            return False

        write_markers = [
            "write_text(",
            "write_bytes(",
            "to_csv(",
            "to_excel(",
            "to_json(",
            "json.dump(",
            "savefig(",
            "document.save(",
            "open(",
        ]

        if "open(" in normalized:
            if any(mode in normalized for mode in ["'w'", '"w"', "'a'", '"a"', "'wb'", '"wb"']):
                return False

        return not any(marker in normalized for marker in write_markers if marker != "open(")

    async def _handle_completion_loop(
        self,
        code: str,
        subtask_title: str,
        expected_result_file: Path,
        tool_id: str,
    ) -> bool:
        is_valid, _ = self._validate_structured_result_file(expected_result_file, subtask_title)
        if not is_valid:
            return False

        if not self._looks_like_non_mutating_summary_code(code):
            return False

        logger.warning("检测到已完成任务仍在执行只读收尾脚本，强制要求模型直接输出最终结论")
        await self.append_chat_history(
            {
                "role": "tool",
                "tool_call_id": tool_id,
                "name": "execute_code",
                "content": (
                    f"{expected_result_file.name} 已存在且校验通过。"
                    "你本轮请求的代码只是在读取现有结果并打印检查/总结，不会生成新的结果文件。"
                    "不要再调用 execute_code；请直接输出最终结果摘要、关键结论、生成文件和风险提示。"
                ),
            }
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "停止执行收尾检查或最终总结代码。"
                    "当前子任务已经具备有效的结构化结果文件，请直接给出最终自然语言结论，"
                    "不要继续调用 execute_code。"
                ),
            }
        )
        return True

    @staticmethod
    def _installed_package_summary(limit: int = 80) -> str:
        preferred = [
            "numpy", "pandas", "scipy", "matplotlib", "seaborn", "openpyxl",
            "pypdf", "python-docx", "aiofiles", "fastapi", "pydantic",
        ]
        installed = {dist.metadata.get("Name", "") for dist in importlib.metadata.distributions()}
        ordered = [name for name in preferred if name in installed]
        remaining = sorted(name for name in installed if name and name not in ordered)
        package_names = ordered + remaining
        if len(package_names) > limit:
            package_names = package_names[:limit] + ["..."]
        return ", ".join(package_names)

    @staticmethod
    def _validate_structured_result_file(file_path: Path, expected_section: str) -> tuple[bool, str]:
        if not file_path.exists() or not file_path.is_file():
            return False, "结构化结果文件不存在"

        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"结构化结果文件不是有效 JSON: {exc}"

        if not isinstance(payload, dict):
            return False, "结构化结果文件顶层必须是 JSON 对象"

        required_keys = ["section", "summary", "key_results", "generated_files", "warnings"]
        missing = [key for key in required_keys if key not in payload]
        if missing:
            return False, f"结构化结果文件缺少字段: {', '.join(missing)}"

        if not isinstance(payload.get("summary"), str) or not payload.get("summary", "").strip():
            return False, "summary 必须是非空字符串"

        if not isinstance(payload.get("key_results"), list):
            return False, "key_results 必须是数组"

        if not isinstance(payload.get("generated_files"), list):
            return False, "generated_files 必须是数组"

        if not isinstance(payload.get("warnings"), list):
            return False, "warnings 必须是数组"

        if payload.get("section") != expected_section:
            return False, f"section 字段必须为 {expected_section}"

        key_results = payload.get("key_results", [])
        if not key_results:
            return False, "key_results 不能为空"

        status_allowed = {"verified", "mismatch", "blocked", "unverified"}
        verification_section = expected_section == "数值复核"

        for index, item in enumerate(key_results, start=1):
            if not isinstance(item, dict):
                return False, f"key_results[{index}] 必须是对象"

            common_required = ["id", "name", "verified", "status", "warnings"]
            common_missing = [key for key in common_required if key not in item]
            if common_missing:
                return False, f"key_results[{index}] 缺少字段: {', '.join(common_missing)}"

            if str(item.get("status", "")).strip() not in status_allowed:
                return False, f"key_results[{index}].status 非法"

            if not isinstance(item.get("warnings"), list):
                return False, f"key_results[{index}].warnings 必须是数组"

            if verification_section:
                required = ["unit", "source_data"]
                missing_fields = [key for key in required if key not in item]
                if missing_fields:
                    return False, f"key_results[{index}] 缺少字段: {', '.join(missing_fields)}"

                if not isinstance(item.get("source_data"), list):
                    return False, f"key_results[{index}].source_data 必须是数组"

                has_verification_value = any(
                    item.get(key) not in {None, "", "见论文", "无法确认"}
                    for key in ["paper_value", "value", "computed_value"]
                )
                if not has_verification_value:
                    return False, f"key_results[{index}] 缺少可复核的 paper_value/value/computed_value"

                if item.get("status") == "verified" and not item.get("source_data"):
                    return False, f"key_results[{index}] 为 verified 时 source_data 不能为空"
            else:
                required = [
                    "value",
                    "unit",
                    "formula",
                    "inputs",
                    "source_data",
                    "code_cell",
                    "evidence",
                    "source",
                ]
                missing_fields = [key for key in required if key not in item]
                if missing_fields:
                    return False, f"key_results[{index}] 缺少字段: {', '.join(missing_fields)}"

                if not isinstance(item.get("inputs"), dict):
                    return False, f"key_results[{index}].inputs 必须是对象"

                if not isinstance(item.get("source_data"), list):
                    return False, f"key_results[{index}].source_data 必须是数组"

                if item.get("status") == "verified":
                    if item.get("value") in {None, "", "见论文", "无法确认"}:
                        return False, f"key_results[{index}] 为 verified 时 value 不能为空且不能是占位文本"
                    if not item.get("source_data"):
                        return False, f"key_results[{index}] 为 verified 时 source_data 不能为空"
                    if not str(item.get("code_cell", "")).strip() and not str(item.get("source", "")).strip():
                        return False, f"key_results[{index}] 为 verified 时 code_cell 或 source 至少一个非空"

        return True, ""

    async def _ensure_structured_result_contract(self, subtask_title: str) -> None:
        result_filename = self._structured_result_filename(subtask_title)
        installed_packages = self._installed_package_summary()
        contract_prompt = (
            f"当前子任务必须遵守以下执行契约：\n"
            f"1. 只能使用当前环境已安装的库；已知可用包示例：{installed_packages}\n"
            "2. 如果需要第三方库但环境未安装，必须立刻改用标准库、已安装库或已有结果文件，不能继续依赖不存在的包。\n"
            f"3. 在结束前，必须在工作目录写出结构化结果文件 {result_filename}。\n"
            "4. 该 JSON 文件必须包含字段：section、summary、key_results、generated_files、warnings。\n"
            f"5. section 字段必须严格等于 {subtask_title}。\n"
            "6. key_results 必须是数组，且不能为空。\n"
            "7. 若当前子任务不是数值复核，则每个 key_result 至少应包含 id、name、value、unit、formula、inputs、source_data、code_cell、evidence、source、verified、status、warnings。\n"
            "8. 若当前子任务是数值复核，则每个 key_result 至少应包含 id、name、paper_value、computed_value 或 value、unit、source_data、verified、status、warnings。\n"
            "9. 对 status=verified 的条目，禁止使用 见论文/无法确认 这类占位值；必须给出真实 value 和 source_data。\n"
            "10. 若结果不可靠，必须写入 warnings，并在 key_results 中显式标注 mismatch 或 blocked。"
        )
        await self.append_chat_history({"role": "user", "content": contract_prompt})

    async def _soft_reset_retry_limit(self, last_error_message: str):
        logger.warning(f"达到最大尝试次数，重置计数并继续: {self.max_retries}")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="代码手达到最大反思次数，已切换为保守求解策略继续执行",
                type="warning",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "你已经多次因为同一类错误反复失败。不要重复之前的失败方案。"
                    "请基于现有上下文继续完成当前子任务，优先采用更稳健、更简化、更少依赖的实现路径。"
                    "只有在原始子任务要求已经全部满足时，才停止调用工具并输出最终结论。"
                    f"最后一次错误信息如下：{last_error_message}"
                ),
            }
        )

    async def _soft_reset_chat_limit(self):
        logger.warning(f"达到最大聊天次数，重置计数并继续: {self.max_chat_turns}")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="代码手达到轮次上限，已压缩思路并继续完成当前子任务",
                type="warning",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "你已经进行了很多轮对话。"
                    "请停止重复尝试，基于当前已有结果收敛到一个可执行且可交付的方案，"
                    "必要时采用更简单的近似、降级实现或分步完成策略。"
                    "但只有在原始子任务要求已经全部满足时，才输出最终结果并停止调用工具。"
                ),
            }
        )

    async def _warn_near_tool_budget(
        self,
        total_tool_calls: int,
        subtask_title: str,
        expected_result_file: Path,
    ) -> None:
        remaining = self.max_total_tool_calls - total_tool_calls
        minimum_delivery_contract = (
            "最小可交付结果必须包含："
            "1) 已经算出的关键数值表或参数表；"
            "2) 每个数值对应的 source_data；"
            "3) 已生成文件清单；"
            "4) 未完成项和原因。"
            "如果完整模型尚未完成，也要先交付这些已确认结果，禁止把整题概括为全部未计算。"
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"代码手接近工具调用上限，剩余 {remaining} 次，将进入最小数值交付模式。",
                type="warning",
                agent="coder",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    f"你距离工具调用上限只剩 {remaining} 次。"
                    "现在必须切换到最小数值交付模式。"
                    f"下一次 execute_code 必须优先写入或修复 {expected_result_file.name}，而不是继续探索模型、出图或打印检查。"
                    f"{minimum_delivery_contract}"
                    "禁止为了字体、标签语言、图例位置、排版美观、重复打印检查或重复读取整张工作表而再次调用 execute_code。"
                    "如果已有图已经生成，不要仅因中文字体警告或样式问题重新出图。"
                    f"若 {subtask_title} 仍有未完成子问题，只保留真正影响最终数值结论的必要计算。"
                ),
            }
        )

    async def _force_blocked_result(
        self,
        expected_result_file: Path,
        subtask_title: str,
        reason: str,
    ) -> CoderToWriter:
        blocked_id = f"{self._sanitize_section_name(subtask_title)}_blocked"
        blocked_result = {
            "id": blocked_id,
            "name": f"{subtask_title}执行状态",
            "value": "blocked",
            "paper_value": "",
            "computed_value": "blocked",
            "unit": "",
            "formula": "",
            "inputs": {},
            "source_data": [],
            "code_cell": "",
            "evidence": reason,
            "source": "coder_guardrail",
            "verified": False,
            "status": "blocked",
            "warnings": [reason],
        }

        existing_payload: dict[str, object] = {}
        if expected_result_file.exists():
            try:
                existing_payload = json.loads(expected_result_file.read_text(encoding="utf-8"))
            except Exception:
                existing_payload = {}

        existing_key_results = existing_payload.get("key_results", [])
        if not isinstance(existing_key_results, list):
            existing_key_results = []

        filtered_key_results = [
            item for item in existing_key_results if isinstance(item, dict) and item.get("id") != blocked_id
        ]
        if not any(item.get("status") == "verified" for item in filtered_key_results if isinstance(item, dict)):
            filtered_key_results.extend(
                self._salvage_existing_payload_key_results(
                    existing_payload,
                    expected_result_file.name,
                    subtask_title,
                )
            )
            filtered_key_results.extend(
                self._salvage_artifact_key_results(self.work_dir, subtask_title)
            )
        filtered_key_results.append(blocked_result)

        existing_generated_files = existing_payload.get("generated_files", [])
        if not isinstance(existing_generated_files, list):
            existing_generated_files = []
        existing_generated_files = [
            *existing_generated_files,
            *self._list_existing_artifacts(self.work_dir),
        ]

        existing_warnings = existing_payload.get("warnings", [])
        if not isinstance(existing_warnings, list):
            existing_warnings = []

        existing_summary = str(existing_payload.get("summary", "") or "").strip()
        blocked_summary = f"{subtask_title} 被阻断：{reason}"
        if existing_summary and blocked_summary not in existing_summary:
            summary = f"{existing_summary}\n\n{blocked_summary}"
        else:
            summary = blocked_summary

        payload = {
            "section": existing_payload.get("section") or subtask_title,
            "summary": summary,
            "key_results": filtered_key_results,
            "generated_files": self._dedupe_jsonish_items(existing_generated_files),
            "warnings": self._dedupe_jsonish_items([*existing_warnings, reason]),
        }
        expected_result_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=reason,
                type="warning",
                agent="coder",
            ),
        )
        # 被阻断时仍返回当前工作目录中已有的图片，避免丢失已生成的图表
        blocked_images = await self.code_interpreter.get_created_images(subtask_title) if self.code_interpreter else []
        return CoderToWriter(
            coder_response=f"{subtask_title} 已阻断：{reason}",
            created_images=blocked_images,
            structured_result_files=[expected_result_file.name],
        )

    async def _handle_repeated_code(self, code: str, subtask_title: str) -> bool:
        """连续重复执行同一段代码时，强制模型收敛而不是无休止重跑。"""
        if code == self.last_executed_code:
            self.same_code_repeat_count += 1
        else:
            self.last_executed_code = code
            self.same_code_repeat_count = 1

        if self.same_code_repeat_count < 3:
            return False

        logger.warning("检测到相同代码被连续重复执行，要求模型直接收口输出")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="检测到代码手重复执行相同代码，已要求其停止重跑并直接总结结果",
                type="warning",
                agent="coder",
            ),
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    f"你刚刚已经连续多次执行完全相同的代码。不要再次执行这段代码。"
                    f"请基于现有输出判断{subtask_title}是否已经完成："
                    "若已完成，直接输出最终结果摘要、关键结论、生成文件和后续建议；"
                    "若未完成，只允许提出下一步与当前代码不同的必要动作。"
                ),
            }
        )
        return True

    async def run(self, prompt: str, subtask_title: str) -> CoderToWriter:
        logger.info(f"{self.__class__.__name__}:开始:执行子任务: {subtask_title}")
        if self.code_interpreter:
            self.code_interpreter.add_section(subtask_title)
        self.current_chat_turns = 0

        # 如果是第一次运行，则添加系统提示
        if self.is_first_run:
            logger.info("首次运行，添加系统提示和数据集文件信息")
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
            # 当前数据集文件
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": f"当前文件夹下的数据集文件\n{get_current_files(self.work_dir, 'data')}",
                }
            )

        await self._ensure_structured_result_contract(subtask_title)

        # 添加 sub_task
        logger.info(f"添加子任务提示: {prompt}")
        await self.append_chat_history({"role": "user", "content": prompt})

        retry_count = 0
        total_retry_count = 0
        total_tool_calls = 0
        last_error_message = ""
        structured_result_retry_count = 0
        near_tool_budget_warned = False
        expected_result_file = Path(self.work_dir) / self._structured_result_filename(subtask_title)
        started_at = time.monotonic()

        while True:
            elapsed = time.monotonic() - started_at
            if elapsed >= self.max_wall_seconds:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大执行时间 {self.max_wall_seconds} 秒，任务被阻断",
                )

            if total_tool_calls >= self.max_total_tool_calls:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大工具调用次数 {self.max_total_tool_calls}，任务被阻断",
                )

            if total_retry_count >= self.max_retries:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"超过最大错误重试次数 {self.max_retries}，任务被阻断",
                )

            if retry_count >= self.max_retries:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"连续错误重试达到上限 {self.max_retries}，最后错误：{last_error_message}",
                )

            if self.current_chat_turns >= self.max_chat_turns:
                return await self._force_blocked_result(
                    expected_result_file,
                    subtask_title,
                    f"达到最大对话轮次 {self.max_chat_turns}，任务被阻断",
                )

            self.current_chat_turns += 1
            logger.info(f"当前对话轮次: {self.current_chat_turns}")

            try:
                response = await self.model.chat(
                    history=self.chat_history,
                    tools=coder_tools,
                    tool_choice="auto",
                    agent_name=self.__class__.__name__,
                    sub_title=subtask_title,
                )

                # 如果有工具调用
                if (
                    hasattr(response.choices[0].message, "tool_calls")
                    and response.choices[0].message.tool_calls
                ):
                    logger.info("检测到工具调用")
                    tool_call = response.choices[0].message.tool_calls[0]
                    tool_id = tool_call.id

                    if tool_call.function.name == "execute_code":
                        logger.info(f"调用工具: {tool_call.function.name}")
                        total_tool_calls += 1
                        if (
                            not near_tool_budget_warned
                            and total_tool_calls >= max(2, self.max_total_tool_calls - 3)
                        ):
                            near_tool_budget_warned = True
                            await self._warn_near_tool_budget(
                                total_tool_calls,
                                subtask_title,
                                expected_result_file,
                            )
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=f"代码手调用{tool_call.function.name}工具",
                                agent="coder",
                            ),
                        )

                        code = json.loads(tool_call.function.arguments)["code"]

                        if await self._handle_completion_loop(
                            code,
                            subtask_title,
                            expected_result_file,
                            tool_id,
                        ):
                            continue

                        if await self._handle_repeated_code(code, subtask_title):
                            continue

                        await redis_manager.publish_message(
                            self.task_id,
                            InterpreterMessage(
                                type="code",
                                content=code,
                                section=subtask_title,
                            ),
                        )

                        # 更新对话历史 - 添加助手的响应
                        await self.append_chat_history(
                            response.choices[0].message.model_dump()
                        )
                        logger.info(response.choices[0].message.model_dump())

                        # 执行工具调用
                        logger.info("执行工具调用")
                        (
                            text_to_gpt,
                            error_occurred,
                            error_message,
                        ) = await self.code_interpreter.execute_code(code)

                        # 添加工具执行结果
                        if error_occurred:
                            await self.append_chat_history(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "name": "execute_code",
                                    "content": error_message,
                                }
                            )

                            logger.warning(f"代码执行错误: {error_message}")
                            retry_count += 1
                            total_retry_count += 1
                            logger.info(f"当前尝试次:{retry_count} / {self.max_retries}")
                            last_error_message = error_message
                            reflection_prompt = get_reflection_prompt(error_message, code)

                            await redis_manager.publish_message(
                                self.task_id,
                                SystemMessage(
                                    content="代码手反思纠正错误",
                                    type="error",
                                    agent="coder",
                                ),
                            )

                            await self.append_chat_history(
                                {"role": "user", "content": reflection_prompt}
                            )
                            continue
                        else:
                            await self.append_chat_history(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_id,
                                    "name": "execute_code",
                                    "content": text_to_gpt,
                                }
                            )
                            await redis_manager.publish_message(
                                self.task_id,
                                InterpreterMessage(
                                    type="result",
                                    content=text_to_gpt,
                                    section=subtask_title,
                                ),
                            )
                            # 成功执行后继续循环，等待下一步指令
                            continue
                else:
                    # 没有工具调用，表示任务完成
                    logger.info("没有工具调用，任务完成")
                    is_valid, validation_error = self._validate_structured_result_file(
                        expected_result_file,
                        subtask_title,
                    )
                    if not is_valid:
                        structured_result_retry_count += 1
                        await self.append_chat_history(
                            {
                                "role": "user",
                                "content": (
                                    f"你还不能结束，因为结构化结果文件校验失败：{validation_error}。"
                                    f"请立即通过执行代码生成或修复 {expected_result_file.name}，"
                                    "然后再给出最终总结。"
                                ),
                            }
                        )
                        if structured_result_retry_count >= 3:
                            return await self._force_blocked_result(
                                expected_result_file,
                                subtask_title,
                                f"未能生成有效的结构化结果文件: {validation_error}",
                            )
                        continue

                    await redis_manager.publish_message(
                        self.task_id,
                        InterpreterMessage(
                            type="result",
                            content=response.choices[0].message.content or "",
                            section=subtask_title,
                        ),
                    )
                    return CoderToWriter(
                        coder_response=response.choices[0].message.content,
                        created_images=await self.code_interpreter.get_created_images(
                            subtask_title
                        ),
                        structured_result_files=[expected_result_file.name],
                    )

            except Exception as e:
                logger.error(f"执行过程中发生异常: {str(e)}")
                retry_count += 1
                total_retry_count += 1
                last_error_message = str(e)
                continue
            logger.info(f"{self.__class__.__name__}:完成:执行子任务: {subtask_title}")
