from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    app_secret_key: str = Field(alias="APP_SECRET_KEY")
    ui_hostname: str = Field(default="localhost", alias="UI_HOSTNAME")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
