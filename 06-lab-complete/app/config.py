"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường (tiêu chí 9).

Biến môi trường (xem .env.example):
    PORT, ENVIRONMENT, LOG_LEVEL, AGENT_API_KEY, REDIS_URL,
    RATE_LIMIT_PER_MINUTE, DAILY_BUDGET_USD, MONTHLY_BUDGET_USD

Không có secret nào được hard-code: AGENT_API_KEY mặc định rỗng, khi đó /ask
từ chối mọi request (fail closed) thay vì chạy với một key mặc định.
"""
import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} phải là số nguyên, nhận được {raw!r}") from e


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} phải là số thực, nhận được {raw!r}") from e


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: _env_str("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO").upper())
    agent_api_key: str = field(default_factory=lambda: _env_str("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: _env_str("REDIS_URL", "redis://localhost:6379/0"))
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    def __post_init__(self):
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE phải > 0")
        if self.daily_budget_usd <= 0 or self.monthly_budget_usd <= 0:
            raise ValueError("DAILY_BUDGET_USD / MONTHLY_BUDGET_USD phải > 0")


settings = Settings()
