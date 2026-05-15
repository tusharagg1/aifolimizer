from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    supabase_url: str = ""
    supabase_service_key: str = ""

    # Local-only WS credentials — never deployed to cloud
    ws_email: str = ""
    ws_password: str = ""

    # LLM provider keys — optional, all free tiers, at least one recommended
    github_token: str = ""          # GitHub Models (GPT-4o-mini, free with Pro)
    google_api_key: str = ""        # Google AI Studio (Gemini Flash, free)
    openrouter_api_key: str = ""    # OpenRouter (Llama 3.3 70B free tier)
    dashscope_api_key: str = ""     # Qwen / DashScope (Qwen-Plus)


settings = Settings()
