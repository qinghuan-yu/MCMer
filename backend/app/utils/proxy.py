import os


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def should_ignore_system_proxy(default: bool = True) -> bool:
    raw = os.getenv("IGNORE_SYSTEM_PROXY")
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def clear_proxy_env() -> list[str]:
    cleared: list[str] = []
    for key in PROXY_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared.append(key)
    return cleared
