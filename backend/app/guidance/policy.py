"""Non-negotiable runtime policy for high-trust guidance tasks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GuidanceRuntimePolicy:
    output_format: str = "markdown"
    run_verification_agent: bool = False
    run_chart_agent: bool = False
    run_llm_figure_repair: bool = False
    run_final_audit_repair_agents: bool = False
    export_docx: bool = False


GUIDANCE_RUNTIME_POLICY = GuidanceRuntimePolicy()

