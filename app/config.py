from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase Transaction pooler connection string; real value comes from .env
    database_url: str = "postgresql+psycopg://postgres.your-project-ref:your-db-password@aws-0-your-region.pooler.supabase.com:6543/postgres"
    openweather_api_key: str = ""
    gemini_api_key: str = ""
    google_places_api_key: str = ""


settings = Settings()
