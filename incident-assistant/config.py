from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(THIS_DIR / ".env"), extra="ignore")

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    # Pinned to a dated model name rather than a "-latest" alias: gemini-1.5-flash was
    # retired between this project being scoped and being built, and the free-tier
    # "-latest" alias hit 503s under load in testing where the dated name did not.
    # Whichever you pin will eventually be retired too — check
    # https://ai.google.dev/gemini-api/docs/models when this 404s.
    gemini_model: str = Field("gemini-3.6-flash", alias="GEMINI_MODEL")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    incidents_dir: str = Field(str(REPO_ROOT / "incidents"), alias="INCIDENTS_DIR")
    runbooks_dir: str = Field(str(REPO_ROOT / "runbooks"), alias="RUNBOOKS_DIR")

    @property
    def incidents_path(self) -> Path:
        return Path(self.incidents_dir)

    @property
    def runbooks_path(self) -> Path:
        return Path(self.runbooks_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
