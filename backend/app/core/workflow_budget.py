from dataclasses import dataclass

from app.config.setting import settings


DEFAULT_WORKFLOW_MODE = "standard"
SUPPORTED_WORKFLOW_MODES = frozenset({"fast", DEFAULT_WORKFLOW_MODE, "strict"})


def normalize_workflow_mode(mode: str | None, fallback: str = DEFAULT_WORKFLOW_MODE) -> str:
    normalized = str(mode or fallback or DEFAULT_WORKFLOW_MODE).strip().lower()
    return normalized if normalized in SUPPORTED_WORKFLOW_MODES else DEFAULT_WORKFLOW_MODE


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
class GuidanceStageBudget:
    decomposition: int
    architecture: int
    indexed_core: int
    indexed_constraints: int
    review: int


@dataclass(frozen=True)
class WorkflowBudget:
    mode: str
    guidance: GuidanceStageBudget
    coder: CoderStageBudget
    solver: CoderStageBudget
    writer: WriterStageBudget
    allow_chart_recovery_without_verified_results: bool


def resolve_workflow_budget(mode: str) -> WorkflowBudget:
    normalized = normalize_workflow_mode(mode)
    if normalized == "fast":
        return WorkflowBudget(
            mode="fast",
            guidance=GuidanceStageBudget(
                decomposition=60,
                architecture=90,
                indexed_core=120,
                indexed_constraints=180,
                review=90,
            ),
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
            guidance=GuidanceStageBudget(
                decomposition=max(300, settings.LLM_REQUEST_TIMEOUT),
                architecture=max(420, settings.LLM_REQUEST_TIMEOUT),
                indexed_core=max(600, settings.LLM_REQUEST_TIMEOUT),
                indexed_constraints=max(720, settings.LLM_REQUEST_TIMEOUT),
                review=max(420, settings.LLM_REQUEST_TIMEOUT),
            ),
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
                max_chat_turns=8,
                sectioned=False,
            ),
            allow_chart_recovery_without_verified_results=True,
        )

    return WorkflowBudget(
        mode="standard",
        guidance=GuidanceStageBudget(
            decomposition=max(120, settings.LLM_REQUEST_TIMEOUT),
            architecture=max(180, settings.LLM_REQUEST_TIMEOUT),
            indexed_core=max(240, settings.LLM_REQUEST_TIMEOUT),
            indexed_constraints=max(360, settings.LLM_REQUEST_TIMEOUT),
            review=max(180, settings.LLM_REQUEST_TIMEOUT),
        ),
        coder=CoderStageBudget(
            max_chat_turns=max(36, settings.MAX_CHAT_TURNS),
            max_retries=max(6, settings.MAX_RETRIES),
            max_total_tool_calls=max(16, settings.CODER_MAX_TOTAL_TOOL_CALLS),
            max_wall_seconds=max(300, settings.CODER_MAX_WALL_SECONDS),
        ),
        solver=CoderStageBudget(
            max_chat_turns=max(48, settings.MAX_CHAT_TURNS),
            max_retries=max(8, settings.MAX_RETRIES),
            max_total_tool_calls=max(28, settings.SOLVE_CODER_MAX_TOTAL_TOOL_CALLS),
            max_wall_seconds=max(720, settings.SOLVE_CODER_MAX_WALL_SECONDS),
        ),
        writer=WriterStageBudget(
            request_timeout=max(300, settings.LLM_REQUEST_TIMEOUT),
            max_chat_turns=8,
            sectioned=False,
        ),
        allow_chart_recovery_without_verified_results=True,
    )
