"""Application settings, loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TradeCognix"
    environment: str = "development"
    database_url: str = "sqlite:///./tradecognix.db"
    cors_origins: str = "*"  # comma separated; wildcard is refused in production
    # Behind a reverse proxy (Render, Nginx) use X-Forwarded-For for rate-limit keys.
    trust_proxy_headers: bool = False

    # Security
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    fernet_key: str = ""  # generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    admin_emails: str = ""  # comma separated; these users get role=admin on register
    password_min_length: int = 8

    # Simple in-memory rate limits (per process). Format: "<count>/<seconds>".
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/300"  # per IP+email
    rate_limit_login_ip: str = "30/300"  # per IP
    rate_limit_register: str = "10/3600"  # per IP
    rate_limit_public: str = "30/600"  # anonymous parse/backtest per IP

    # Billing (Razorpay). Checkout returns 503 until both keys are set.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Live trading master switch. Live orders need this AND a per-strategy confirmation.
    live_trading_enabled: bool = False
    # While payments are disabled, every account gets this plan's limits (free | pro | elite).
    beta_plan: str = "pro"
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
    default_slippage_pct: Optional[float] = Field(None, description="Slippage as % of price per fill; None = per-segment default (options 0.5, futures 0.01, equity 0.05)")

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]


DEFAULT_JWT_SECRETS = {"", "change-me", "change-me-in-production", "secret", "changeme", "test-secret"}


def production_problems(s: Settings) -> list[str]:
    """Reasons the app must NOT start in production with these settings (empty list = OK)."""
    problems = []
    if s.jwt_secret.strip() in DEFAULT_JWT_SECRETS or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be a random value of at least 32 characters")
    if not s.cors_list or "*" in s.cors_list:
        problems.append("CORS_ORIGINS must list explicit origins (no wildcard) in production")
    if not s.fernet_key:
        problems.append("FERNET_KEY must be set in production (broker credentials are encrypted with it)")
    if s.jwt_algorithm not in ("HS256", "HS384", "HS512"):
        problems.append("JWT_ALGORITHM must be an HMAC algorithm (HS256/HS384/HS512)")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
