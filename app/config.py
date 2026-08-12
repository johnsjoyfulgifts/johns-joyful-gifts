from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-only-insecure-secret-change-me"
    database_url: str = "sqlite:///./johns_joyful_gifts.db"
    upload_dir: str = "./uploads"
    max_upload_size_bytes: int = 5 * 1024 * 1024

    store_name: str = "John's Joyful Gifts"
    store_tagline: str = "Joyful Gifts for Every Little Moment"
    whatsapp_number: str = "+910000000000"
    instagram_url: str = "https://instagram.com/"
    default_delivery_charge: float = 49
    free_delivery_threshold: float = 999

    environment: str = "development"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
