"""
LLM 模块 - 基于 LiteLLM 的多模型支持
"""
import os
import asyncio
from typing import Optional, AsyncGenerator

from app.utils.proxy import clear_proxy_env, should_ignore_system_proxy


if should_ignore_system_proxy():
    clear_proxy_env()

import httpx
import litellm
from litellm import completion, acompletion
from litellm.exceptions import APIError, RateLimitError, ServiceUnavailableError

from app.config.setting import settings
from app.utils.log_util import logger

# 配置 LiteLLM
litellm.drop_params = True
litellm.telemetry = False
_configured_proxy_url: Optional[str] = None


def _get_effective_key(env_key: str, setting_val: str) -> Optional[str]:
    """获取有效配置：运行时 > 环境变量 > settings"""
    try:
        from app.services.config_manager import config_manager
        runtime_val = config_manager.get_effective_key(env_key)
        if runtime_val:
            return runtime_val
    except Exception:
        pass
    return os.environ.get(env_key) or setting_val or None


def _get_llm_proxy_url() -> Optional[str]:
    return _get_effective_key("LLM_PROXY_URL", settings.LLM_PROXY_URL)


def _configure_litellm_http_clients() -> None:
    """为 LiteLLM 配置 HTTP 客户端，默认忽略系统代理，仅使用应用显式代理。"""
    global _configured_proxy_url
    proxy_url = _get_llm_proxy_url()
    if proxy_url == _configured_proxy_url and getattr(litellm, "client_session", None) is not None:
        return

    client_kwargs = {"trust_env": False}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    litellm.client_session = httpx.Client(**client_kwargs)
    litellm.aclient_session = httpx.AsyncClient(**client_kwargs)
    _configured_proxy_url = proxy_url


_configure_litellm_http_clients()


def _infer_provider(model_name: str) -> str:
    """从模型名推断 provider。支持 openai/gpt-4o 或 gpt-4o 两种写法。"""
    model = (model_name or "").strip().lower()
    if not model:
        return ""
    if "/" in model:
        return model.split("/", 1)[0]

    if model.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith("mimo"):
        return "mimo"
    if model.startswith("gemini"):
        return "gemini"
    return ""


def _normalize_deepseek_compat(
    model: str,
    base_url: Optional[str],
    api_key: Optional[str],
) -> tuple[str, Optional[str]]:
    """当用户把 DeepSeek 兼容端点与 OpenAI/Anthropic 模型混用时，自动切换到 DeepSeek V4 模型。"""
    # MiMo 模型不应被 DeepSeek 兼容逻辑覆盖
    if _infer_provider(model) == "mimo":
        return model, api_key

    base = (base_url or "").lower()
    if "deepseek.com" not in base:
        return model, api_key

    normalized_model = model
    provider = _infer_provider(model)
    if provider in {"openai", "anthropic"} and "deepseek-v4" not in (model or "").lower():
        normalized_model = "deepseek/deepseek-v4-flash"
        logger.warning(
            "检测到 DeepSeek 兼容端点与非 DeepSeek 模型组合，已自动切换为 deepseek/deepseek-v4-flash"
        )

    # DeepSeek 路径优先使用 DEEPSEEK_API_KEY
    normalized_key = api_key
    if _infer_provider(normalized_model) == "deepseek":
        deepseek_key = _get_effective_key("DEEPSEEK_API_KEY", settings.DEEPSEEK_API_KEY)
        if deepseek_key:
            normalized_key = deepseek_key

    return normalized_model, normalized_key


def _normalize_mimo_compat(
    model: str,
    base_url: Optional[str],
    api_key: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """将 mimo/ 前缀模型转换为 openai/ 前缀，使用 MiMo 的 OpenAI 兼容端点。"""
    provider = _infer_provider(model)
    if provider != "mimo":
        return model, api_key, base_url

    # mimo/mimo-v2.5-pro → openai/mimo-v2.5-pro
    model_name = model.split("/", 1)[1] if "/" in model else model
    normalized_model = f"openai/{model_name}"

    # 强制使用 MiMo base_url（不使用全局 OPENAI_BASE_URL）
    mimo_base_url = _get_effective_key("MIMO_BASE_URL", settings.MIMO_BASE_URL)
    if mimo_base_url:
        base_url = mimo_base_url

    # 自动获取 MiMo api_key
    if not api_key:
        api_key = _get_effective_key("MIMO_API_KEY", settings.MIMO_API_KEY)

    logger.info(f"MiMo 模型映射: {model} → {normalized_model}, base_url={base_url}")
    return normalized_model, api_key, base_url


class LLM:
    """LLM 封装类，支持 litellm 所有模型"""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        request_timeout: Optional[int] = None,
    ):
        _configure_litellm_http_clients()
        self.model = model or _get_effective_key("DEFAULT_MODEL", settings.DEFAULT_MODEL)
        # 自动从运行时配置获取 API Key
        if not api_key:
            provider = _infer_provider(self.model or "")
            if provider == "openai":
                api_key = _get_effective_key("OPENAI_API_KEY", settings.OPENAI_API_KEY)
            elif provider == "anthropic":
                api_key = _get_effective_key("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY)
            elif provider == "deepseek":
                api_key = _get_effective_key("DEEPSEEK_API_KEY", settings.DEEPSEEK_API_KEY)
            elif provider == "mimo":
                api_key = _get_effective_key("MIMO_API_KEY", settings.MIMO_API_KEY)
                if not base_url:
                    base_url = _get_effective_key("MIMO_BASE_URL", settings.MIMO_BASE_URL)
        # MiMo 模型不应使用全局 OPENAI_BASE_URL
        if not base_url or _infer_provider(self.model or "") == "mimo":
            if _infer_provider(self.model or "") == "mimo":
                base_url = _get_effective_key("MIMO_BASE_URL", settings.MIMO_BASE_URL)
            else:
                base_url = _get_effective_key("OPENAI_BASE_URL", settings.OPENAI_BASE_URL)

        self.model, api_key = _normalize_deepseek_compat(
            model=self.model,
            base_url=base_url,
            api_key=api_key,
        )

        self.model, api_key, base_url = _normalize_mimo_compat(
            model=self.model,
            base_url=base_url,
            api_key=api_key,
        )

        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout or settings.LLM_REQUEST_TIMEOUT

    async def chat(
        self,
        history: list[dict],
        tools: Optional[list] = None,
        tool_choice: str = "auto",
        agent_name: str = "",
        sub_title: str = "",
    ):
        """异步对话"""
        kwargs = {
            "model": self.model,
            "messages": history,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.request_timeout,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        for attempt in range(3):
            try:
                response = await asyncio.wait_for(
                    acompletion(**kwargs),
                    timeout=self.request_timeout + 5,
                )
                return response
            except asyncio.TimeoutError:
                wait = 2 ** attempt
                logger.warning(
                    f"{agent_name}: 请求超时, 第{attempt+1}次重试, "
                    f"等待{wait}s"
                )
                if attempt == 2:
                    raise RuntimeError(
                        f"{agent_name or 'LLM'}: 请求超时，超过 {self.request_timeout}s"
                    )
                await asyncio.sleep(wait)
            except (RateLimitError, ServiceUnavailableError) as e:
                wait = 2 ** attempt
                logger.warning(
                    f"{agent_name}: {type(e).__name__}, 第{attempt+1}次重试, "
                    f"等待{wait}s"
                )
                await asyncio.sleep(wait)
            except APIError as e:
                logger.error(f"{agent_name}: API错误 - {e}")
                if attempt == 2:
                    raise
                await asyncio.sleep(2)

        raise RuntimeError(f"{agent_name}: 重试耗尽")

    async def chat_stream(
        self,
        history: list[dict],
        agent_name: str = "",
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        kwargs = {
            "model": self.model,
            "messages": history,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            "timeout": self.request_timeout,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url

        try:
            response = await asyncio.wait_for(
                acompletion(**kwargs),
                timeout=self.request_timeout + 5,
            )
            async for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except asyncio.TimeoutError:
            logger.error(f"{agent_name}: 流式对话超时")
            yield f"\n[错误: 请求超时，超过 {self.request_timeout}s]"
        except Exception as e:
            logger.error(f"{agent_name}: 流式对话错误 - {e}")
            yield f"\n[错误: {str(e)}]"

    def chat_sync(self, history: list[dict]) -> str:
        """同步对话（用于简单场景）"""
        try:
            response = completion(
                model=self.model,
                messages=history,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.request_timeout,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"同步对话错误: {e}")
            return f"错误: {str(e)}"


async def simple_chat(
    messages: list[dict],
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """简单对话工具函数"""
    llm = LLM(model=model, max_tokens=max_tokens)
    response = await llm.chat(history=messages)
    return response.choices[0].message.content
