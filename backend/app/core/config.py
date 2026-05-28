from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Local-only WS credentials — never deployed to cloud
    ws_email: str = ""
    ws_password: str = ""

    # Local TimescaleDB + Redis (Phase 0)
    postgres_dsn: str = (
        "postgresql://aifolimizer:aifolimizer@localhost:5432/aifolimizer"
    )
    redis_url: str = "redis://localhost:6379/0"

    # Push notifications (Phase 4)
    ntfy_topic: str = ""

    # Error tracking (Phase 15 — opt-in, empty = disabled)
    sentry_dsn: str = ""
    sentry_auth_token: str = ""
    sentry_org: str = ""
    sentry_project: str = ""
    environment: str = "dev"
    app_version: str = "v4.3"

    # LLM provider keys — optional, all free tiers, at least one recommended
    github_token: str = ""          # GitHub Models (GPT-4o-mini, free with Pro)
    google_api_key: str = ""        # Google AI Studio (Gemini Flash, free)
    openrouter_api_key: str = ""    # OpenRouter (Llama 3.3 70B free tier)
    dashscope_api_key: str = ""     # Qwen / DashScope (Qwen-Plus)


settings = Settings()
