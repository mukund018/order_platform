from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_TRACKED_SKUS = ["SKU-0001", "SKU-0002", "SKU-0003", "SKU-0004", "SKU-0005"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="INVENTORY_DATABASE_URL")
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    product_cache_ttl: int = Field(60, alias="PRODUCT_CACHE_TTL", ge=1)
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE", ge=1)
    db_max_overflow: int = Field(5, alias="DB_MAX_OVERFLOW", ge=0)
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str | None = Field(None, alias="LOG_DIR")

    # Exporting a gauge per SKU would mean one time series per product, so only the
    # SKUs listed here get one.
    tracked_skus: Annotated[list[str], NoDecode] = Field(
        default=DEFAULT_TRACKED_SKUS, alias="TRACKED_SKUS"
    )

    @field_validator("tracked_skus", mode="before")
    @classmethod
    def _split_skus(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
