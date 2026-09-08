from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Bilge Inox Kapasite Planlama"
    database_url: str = "sqlite:///./kapasite_dev.db"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    first_admin_username: str = "admin"
    first_admin_password: str = "admin123"
    first_owner_username: str = "owner"
    first_owner_password: str = "owner123"
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
