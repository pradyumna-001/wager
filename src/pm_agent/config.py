"""Environment-driven configuration.

Access settings ONLY via ``get_settings()`` inside functions. Never read
``os.environ`` directly elsewhere and never instantiate ``Settings()`` at module
import time — that breaks test isolation.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    database_url: str

    # LLM gateway (OpenAI-compatible, e.g. NVIDIA NIM)
    llm_base_url: str
    llm_api_key: str
    llm_model: str

    # Google (service account shared by Docs + Calendar)
    google_service_account_file: str
    google_doc_id: str
    google_calendar_id: str

    # Slack
    slack_bot_token: str
    slack_signing_secret: str
    slack_channel_id: str
    pm_slack_user_id: str

    # GitHub
    github_token: str
    github_repo: str  # owner/name

    # PostHog
    posthog_host: str
    posthog_project_id: str
    posthog_personal_api_key: str

    # Grading / demo
    bet_tolerance_pp: float = 5.0
    demo_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return cached settings. Tests: `get_settings.cache_clear()` after env patch."""
    return Settings()
