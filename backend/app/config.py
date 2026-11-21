"""Application settings, loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TradeCognix"
    environment: str = "development"
    database_url: str = "sqlite:///./tradecognix.db"
    cors_origins: str = "*"  # comma separated

    # Security
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    fernet_key: str = ""  # generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    admin_emails: str = ""  # comma separated; these users get role=admin on register

    # Live trading master switch. Live orders need this AND a per-strategy confirmation.
    live_trading_enabled: bool = False
    # Public static IP whitelisted with your broker (informational, shown in UI / checked at startup)
    static_ip: str = ""

    # Default Angel One credentials (optional; per-user credentials are stored encrypted in DB)
    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_pin: str = ""
    angel_totp_secret: str = ""

    # LLM (optional). Gemini free tier or any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter...).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "llama3.1"
    llm_timeout_seconds: float = 20.0

    # Engine
    scheduler_enabled: bool = True
    paper_starting_capital: float = 500000.0
    default_slippage_pct: float = Field(0.5, description="Slippage as % of premium per fill")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
