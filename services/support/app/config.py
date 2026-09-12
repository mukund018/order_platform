from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The same directory every service writes its JSON log to. Mounted read-only here:
    # this service reads the platform's logs, it has no business editing them.
    log_dir: str = Field("/app/logs", alias="LOG_DIR")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    orders_url: str = Field("http://orders:8001", alias="ORDERS_URL")
    inventory_url: str = Field("http://inventory:8002", alias="INVENTORY_URL")
    payments_url: str = Field("http://payments:8003", alias="PAYMENTS_URL")

    # Checking whether a service is up must never take longer than a person is willing
    # to watch a dashboard spinner, and a hung dependency is itself the answer.
    probe_timeout_s: float = Field(2.0, alias="SUPPORT_PROBE_TIMEOUT_S")

    # Reading every log line on every request is fine at this scale and wrong at any
    # other, so the window is capped rather than unbounded.
    max_records: int = Field(50_000, alias="SUPPORT_MAX_RECORDS")

    incidents_dir: str = Field("/app/incidents", alias="SUPPORT_INCIDENTS_DIR")

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir)

    @property
    def incidents_path(self) -> Path:
        return Path(self.incidents_dir)

    @property
    def services(self) -> dict[str, str]:
        return {
            "orders": self.orders_url,
            "inventory": self.inventory_url,
            "payments": self.payments_url,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
