from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    triage_mode: Literal["demo", "live"] = "demo"
    publish_mode: Literal["draft", "github"] = "draft"
    database_url: str = "sqlite:///.local/triage.sqlite3"
    webhook_secret: SecretStr
    internal_api_key: SecretStr
    allowed_repositories: str = "demo/ci-sandbox"
    github_token: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    auto_close_on_success: bool = False
    n8n_dispatch_url: str = "http://n8n:5678/webhook/ci-triage"
    lease_seconds: int = 600
    max_processing_attempts: int = 5
    max_webhook_bytes: int = 1_048_576
    max_log_bytes: int = 1_048_576
    max_failed_jobs: int = 20
    request_timeout_seconds: float = 25

    @property
    def repository_allowlist(self) -> set[str]:
        return {
            repo.strip().lower() for repo in self.allowed_repositories.split(",") if repo.strip()
        }

    @property
    def secret_values(self) -> list[str]:
        return [
            value.get_secret_value()
            for value in (
                self.webhook_secret,
                self.internal_api_key,
                self.github_token,
                self.telegram_bot_token,
            )
            if value.get_secret_value()
        ]

    @model_validator(mode="after")
    def validate_configuration(self):
        if any(
            len(value.get_secret_value()) < 32
            for value in (self.webhook_secret, self.internal_api_key)
        ):
            raise ValueError("WEBHOOK_SECRET and INTERNAL_API_KEY must have at least 32 characters")
        if not self.repository_allowlist:
            raise ValueError("ALLOWED_REPOSITORIES must not be empty")
        if self.triage_mode == "demo" and self.publish_mode != "draft":
            raise ValueError("Demo mode must use draft publication")
        if self.triage_mode == "live" and not self.github_token.get_secret_value():
            raise ValueError("Live mode requires GITHUB_TOKEN with Actions: read")
        if bool(self.telegram_bot_token.get_secret_value()) != bool(self.telegram_chat_id):
            raise ValueError("Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or neither")
        if self.lease_seconds < 60 or self.max_processing_attempts < 1:
            raise ValueError(
                "Lease must be at least 60 seconds; processing attempts must be positive"
            )
        return self
