"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường (tiêu chí 9).

- Chạy local: đặt biến trong file .env (không commit), load_dotenv() đọc vào.
- Chạy Docker / cloud: biến do platform cấp (Railway Variables, Render envVars...).
  load_dotenv() KHÔNG ghi đè biến đã có sẵn trong môi trường, nên platform luôn thắng.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # default_factory: đọc env lúc tạo Settings(), không phải lúc định nghĩa class
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))

    # Secret: KHÔNG có giá trị mặc định trong code
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))

    def validate(self) -> "Settings":
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE phải > 0")
        if self.daily_budget_usd <= 0:
            raise ValueError("DAILY_BUDGET_USD phải > 0")
        # Fail fast: production mà quên đặt key thì dừng ngay, không chạy "mở cửa"
        if self.environment == "production" and not self.agent_api_key:
            raise ValueError("AGENT_API_KEY bắt buộc khi ENVIRONMENT=production")
        return self


settings = Settings().validate()
