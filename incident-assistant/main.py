"""incident-assistant (:8005) — ask Gemini about this platform's own closed incidents.

Not a general chatbot: the system prompt is grounded entirely in incidents/INC-*/rca.md
and runbooks/*.md (see context_loader.py), and is explicitly told to say so rather than
fall back on general knowledge when the history does not cover a query. It owns no
database and writes nothing, the same shape as support-service.
"""

import json
from pathlib import Path

from config import get_settings
from context_loader import build_context
from fastapi import FastAPI
from fastapi.responses import FileResponse
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from common.errors import DependencyUnavailableError, UpstreamError, install_exception_handlers
from common.health import build_health_router
from common.logging import configure_logging, get_logger
from common.metrics import build_metrics_router
from common.middleware import install_platform_middleware

SERVICE_NAME = "incident-assistant"
UI_DIR = Path(__file__).resolve().parent / "ui"

log = get_logger(__name__)


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    root_cause: str
    suggested_fix: str
    escalation: str


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "escalation": {"type": "string"},
    },
    "required": ["root_cause", "suggested_fix", "escalation"],
}

_SYSTEM_PROMPT_HEADER = (
    "You are the incident assistant for the order-platform system. Answer ONLY from "
    "the incident history and runbooks below — this is the platform's own closed-"
    "incident record, not general sysadmin knowledge. If nothing below covers the "
    "query, say so honestly in root_cause instead of guessing.\n\n"
    "Respond with JSON only, matching exactly these three keys: "
    "root_cause, suggested_fix, escalation."
)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=None)

    client = genai.Client(api_key=settings.gemini_api_key)

    app = FastAPI(
        title="incident-assistant",
        version="0.1.0",
        summary="Ask Gemini about the platform's own closed incidents and runbooks",
    )
    app.include_router(build_health_router())
    app.include_router(build_metrics_router())
    install_exception_handlers(app)
    install_platform_middleware(app, service=SERVICE_NAME)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.post("/ask", response_model=AskResponse)
    def ask(payload: AskRequest) -> AskResponse:
        context = build_context(settings.incidents_path, settings.runbooks_path)
        system_prompt = f"{_SYSTEM_PROMPT_HEADER}\n\n{context}"

        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=payload.query,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                ),
            )
        except genai_errors.ServerError as exc:
            raise DependencyUnavailableError(f"gemini is unavailable: {exc}") from exc
        except genai_errors.ClientError as exc:
            raise UpstreamError(f"gemini rejected the request: {exc}") from exc

        try:
            parsed = json.loads(response.text)
            return AskResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("gemini_response_invalid", raw=(response.text or "")[:500])
            raise UpstreamError("gemini did not return the expected JSON shape") from exc

    return app


app = create_app()
