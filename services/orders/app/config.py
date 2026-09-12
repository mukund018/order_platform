from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="ORDERS_DATABASE_URL")
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(5, alias="DB_MAX_OVERFLOW")

    inventory_url: str = Field("http://inventory:8002", alias="INVENTORY_URL")
    payments_url: str = Field("http://payments:8003", alias="PAYMENTS_URL")
    inventory_timeout_s: float = Field(2.0, alias="INVENTORY_TIMEOUT_S")
    payments_timeout_s: float = Field(3.0, alias="PAYMENTS_TIMEOUT_S")

    celery_broker_url: str = Field("redis://redis:6379/1", alias="CELERY_BROKER_URL")
    # The celery worker has no HTTP API, so it serves prometheus on a bare port of its own.
    metrics_port: int = Field(9100, alias="METRICS_PORT")

    order_expiry_minutes: int = Field(15, alias="ORDER_EXPIRY_MINUTES")
    business_timezone: str = Field("Asia/Kolkata", alias="BUSINESS_TIMEZONE")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str | None = Field(None, alias="LOG_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
