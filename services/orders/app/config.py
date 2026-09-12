from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# INC-001: an outbound timeout below this cannot be met reliably by a healthy dependency.
# It is not a guess at how fast inventory or payments are - both are far quicker than this.
# It is the floor below which the budget stops bounding the dependency's work and starts
# bounding the queueing in front of it, which the caller cannot see and cannot control.
# A timeout that small can only produce failures, so it is a configuration error and the
# right place to find out is at startup, not under customer load.
MIN_UPSTREAM_TIMEOUT_S = 0.5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="ORDERS_DATABASE_URL")
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(5, alias="DB_MAX_OVERFLOW")

    inventory_url: str = Field("http://inventory:8002", alias="INVENTORY_URL")
    payments_url: str = Field("http://payments:8003", alias="PAYMENTS_URL")
    inventory_timeout_s: float = Field(2.0, alias="INVENTORY_TIMEOUT_S", ge=MIN_UPSTREAM_TIMEOUT_S)
    payments_timeout_s: float = Field(3.0, alias="PAYMENTS_TIMEOUT_S", ge=MIN_UPSTREAM_TIMEOUT_S)

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
