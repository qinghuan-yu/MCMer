from dataclasses import dataclass

from app.config.setting import settings


@dataclass(frozen=True)
class CoderStageBudget:
    max_chat_turns: int
    max_retries: int
    max_total_tool_calls: int
    max_wall_seconds: int


@dataclass(frozen=True)
class WriterStageBudget:
    request_timeout: int
    max_chat_turns: int
    sectioned: bool


@dataclass(frozen=True)
class WorkflowBudget:
    mode: str
    llm_timeout: int
    coder: CoderStageBudget
    solver: CoderStageBudget
    writer: WriterStageBudget
    allow_chart_recovery_without_verified_results: bool


def resolve_workflow_budget(mode: str) -> WorkflowBudget:
    normalized = (mode or "standard").strip().lower()
    if normalized == "fast":
        return WorkflowBudget(
            mode="fast",
            llm_timeout=90,
            coder=CoderStageBudget(
                max_chat_turns=min(settings.MAX_CHAT_TURNS, 12),
                max_retries=min(settings.MAX_RETRIES, 3),
                max_total_tool_calls=min(settings.CODER_MAX_TOTAL_TOOL_CALLS, 6),
                max_wall_seconds=min(settings.CODER_MAX_WALL_SECONDS, 120),
            ),
            solver=CoderStageBudget(
                max_chat_turns=min(settings.MAX_CHAT_TURNS, 16),
                max_retries=min(settings.MAX_RETRIES, 4),
                max_total_tool_calls=min(settings.SOLVE_CODER_MAX_TOTAL_TOOL_CALLS, 10),
                max_wall_seconds=min(settings.SOLVE_CODER_MAX_WALL_SECONDS, 240),
            ),
            writer=WriterStageBudget(
                request_timeout=max(240, settings.LLM_REQUEST_TIMEOUT),
                max_chat_turns=6,
                sectioned=False,
            ),
            allow_chart_recovery_without_verified_results=False,
        )

    if normalized == "strict":
        return WorkflowBudget(
            mode="strict",
            llm_timeout=max(300, settings.LLM_REQUEST_TIMEOUT),
            coder=CoderStageBudget(
                max_chat_turns=max(48, settings.MAX_CHAT_TURNS),
                max_retries=max(8, settings.MAX_RETRIES),
                max_total_tool_calls=max(24, settings.CODER_MAX_TOTAL_TOOL_CALLS),
                max_wall_seconds=max(420, settings.CODER_MAX_WALL_SECONDS),
            ),
            solver=CoderStageBudget(
                max_chat_turns=max(60, settings.MAX_CHAT_TURNS),
                max_retries=max(10, settings.MAX_RETRIES),
                max_total_tool_calls=max(36, settings.SOLVE_CODER_MAX_TOTAL_TOOL_CALLS),
                max_wall_seconds=max(720, settings.SOLVE_CODER_MAX_WALL_SECONDS),
            ),
            writer=WriterStageBudget(
                request_timeout=max(420, settings.LLM_REQUEST_TIMEOUT),
                max_chat_turns=10,
                sectioned=True,
            ),
            allow_chart_recovery_without_verified_results=True,
        )

    return WorkflowBudget(
        mode="standard",
        llm_timeout=settings.LLM_REQUEST_TIMEOUT,
        coder=CoderStageBudget(
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            max_total_tool_calls=settings.CODER_MAX_TOTAL_TOOL_CALLS,
            max_wall_seconds=settings.CODER_MAX_WALL_SECONDS,
        ),
        solver=CoderStageBudget(
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            max_total_tool_calls=settings.SOLVE_CODER_MAX_TOTAL_TOOL_CALLS,
            max_wall_seconds=settings.SOLVE_CODER_MAX_WALL_SECONDS,
        ),
        writer=WriterStageBudget(
            request_timeout=max(300, settings.LLM_REQUEST_TIMEOUT),
            max_chat_turns=8,
            sectioned=False,
        ),
        allow_chart_recovery_without_verified_results=True,
    )
