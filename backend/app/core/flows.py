"""
Flow 章节编排器
- 阶段1: 求解阶段 (Coder + Writer 交替)
- 阶段2: 写作阶段 (Writer only)
"""
from typing import OrderedDict as OrderedDictType
from collections import OrderedDict

from app.schemas.A2A import CoordinatorToModeler, ModelerToCoder, CoderToWriter


class Flows:
    """章节流生成器"""

    def __init__(self):
        self.write_order = [
            "firstPage",
            "RepeatQues",
            "analysisQues",
            "modelAssumption",
            "symbol",
            "judge",
        ]

    def get_solution_flows(
        self,
        coordinator: CoordinatorToModeler,
        modeler: ModelerToCoder,
    ) -> OrderedDictType[str, dict]:
        """生成阶段1任务：eda + quesN + sensitivity_analysis
        modeler.questions_solution 为 dict，key 如 "eda","ques1",...,"sensitivity_analysis"，value 为建模方案文字。
        """
        flows: OrderedDictType[str, dict] = OrderedDict()
        solutions = modeler.questions_solution or {}

        for key, solution_text in solutions.items():
            if key == "eda":
                flows["eda"] = {
                    "title": "探索性数据分析",
                    "solution": solution_text,
                    "coder_prompt": (
                        "请先完成数据预处理与探索性分析（EDA）。\n"
                        f"建模方案:\n{solution_text}\n\n"
                        "要求：\n"
                        "1. 自动识别并读取 data 目录中的数据文件\n"
                        "2. 输出数据规模、字段说明、缺失值/异常值分析\n"
                        "3. 完成必要的数据清洗并保存清洗后数据\n"
                        "4. 绘制关键分布图、相关性图或时间趋势图\n"
                        "5. 所有图表标题、坐标轴含义和单位清晰\n"
                        "6. 给出对后续建模有价值的结论"
                    ),
                }
            elif key == "sensitivity_analysis":
                flows["sensitivity_analysis"] = {
                    "title": "敏感性分析",
                    "solution": solution_text,
                    "coder_prompt": (
                        "请基于已建立模型完成敏感性分析。\n"
                        f"建模方案:\n{solution_text}\n\n"
                        "要求：\n"
                        "1. 选择关键参数并设置合理扰动范围\n"
                        "2. 分析结果稳定性与鲁棒性\n"
                        "3. 输出敏感性可视化图表\n"
                        "4. 给出结论与风险提示"
                    ),
                }
            else:
                # ques1, ques2, ...
                flows[key] = {
                    "title": key,
                    "solution": solution_text,
                    "coder_prompt": (
                        f"请完成子问题: {key}\n\n"
                        f"建模方案:\n{solution_text}\n\n"
                        "请产出可复现代码、关键图表和结果结论。"
                    ),
                }

        return flows

    def get_solution_writer_prompt(
        self,
        section_key: str,
        section_meta: dict,
        coder_result: CoderToWriter,
        coordinator: CoordinatorToModeler,
    ) -> str:
        """生成阶段1 writer prompt"""
        title = section_meta.get("title", section_key)
        return (
            f"你现在撰写章节: {section_key} ({title})\n\n"
            f"问题背景:\n{coordinator.questions.background}\n\n"
            f"代码手结果:\n{coder_result.coder_response}\n\n"
            "请生成该章节可直接纳入论文的内容，包含：\n"
            "1. 方法说明\n"
            "2. 结果展示与解释\n"
            "3. 小结\n"
            "写作要求：学术风格、段落化表达、必要时使用 LaTeX 数学公式。"
        )

    def get_write_flows(
        self,
        question: str,
        coordinator: CoordinatorToModeler,
        solution_sections: dict[str, str],
    ) -> OrderedDictType[str, str]:
        """生成阶段2写作任务（纯 Writer）"""
        flows: OrderedDictType[str, str] = OrderedDict()

        joined_solution = "\n\n".join(
            [f"## {k}\n{v}" for k, v in solution_sections.items()]
        )

        flows["firstPage"] = (
            "请生成论文首页内容：题目、摘要、关键词。"
            "摘要需概括方法、结果与结论，不少于300字。"
        )

        flows["RepeatQues"] = f"请撰写‘问题重述’章节。原题如下：\n{question}"

        flows["analysisQues"] = (
            "请撰写‘问题分析’章节，包含建模难点、数据特征、技术路线。\n"
            f"可参考背景：{coordinator.questions.background}"
        )

        flows["modelAssumption"] = (
            "请撰写‘模型假设’章节，要求假设清晰、可解释，并说明合理性。"
        )

        flows["symbol"] = (
            "请撰写‘符号说明’章节，使用表格列出主要符号、含义、单位。"
        )

        flows["judge"] = (
            "请撰写‘模型评价与改进’章节，至少包含：\n"
            "1. 优点\n2. 局限性\n3. 改进方向\n"
            "并综合以下求解章节信息：\n"
            f"{joined_solution[:8000]}"
        )

        return flows

    def build_markdown(self, section_results: dict[str, str]) -> str:
        """按固定顺序拼接论文 Markdown"""
        paper_order = [
            "firstPage",
            "RepeatQues",
            "analysisQues",
            "modelAssumption",
            "symbol",
            "eda",
        ]

        ques_keys = sorted([k for k in section_results.keys() if k.startswith("ques")])
        paper_order.extend(ques_keys)
        paper_order.extend(["sensitivity_analysis", "judge"])

        parts: list[str] = []
        for k in paper_order:
            content = section_results.get(k, "")
            if content and content.strip():
                parts.append(content.strip())

        # 附加可能存在但未在标准顺序中的章节
        for k, v in section_results.items():
            if k not in paper_order and v and v.strip():
                parts.append(v.strip())

        return "\n\n".join(parts).strip() + "\n"
