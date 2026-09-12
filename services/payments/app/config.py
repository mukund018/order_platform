from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="PAYMENTS_DATABASE_URL")
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE", ge=1)
    db_max_overflow: int = Field(5, alias="DB_MAX_OVERFLOW", ge=0)

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str | None = Field(None, alias="LOG_DIR")

    gateway_latency_ms_min: int = Field(50, alias="GATEWAY_LATENCY_MS_MIN", ge=0)
    gateway_latency_ms_max: int = Field(300, alias="GATEWAY_LATENCY_MS_MAX", ge=0)
    gateway_failure_rate: float = Field(0.05, alias="GATEWAY_FAILURE_RATE", ge=0.0, le=1.0)
    gateway_timeout_rate: float = Field(0.0, alias="GATEWAY_TIMEOUT_RATE", ge=0.0, le=1.0)
    # Longer than any caller timeout in the platform, so "timeout mode" really does
    # leave the caller giving up while the gateway is still working.
    gateway_timeout_sleep_s: float = Field(30.0, alias="GATEWAY_TIMEOUT_SLEEP_S", gt=0)
    gateway_seed: int | None = Field(None, alias="GATEWAY_SEED")

    @model_validator(mode="after")
    def _check_latency_bounds(self) -> "Settings":
        if self.gateway_latency_ms_max < self.gateway_latency_ms_min:
            raise ValueError("GATEWAY_LATENCY_MS_MAX must be >= GATEWAY_LATENCY_MS_MIN")
        if self.gateway_timeout_rate + self.gateway_failure_rate > 1.0:
            raise ValueError("GATEWAY_TIMEOUT_RATE + GATEWAY_FAILURE_RATE must be <= 1.0")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
