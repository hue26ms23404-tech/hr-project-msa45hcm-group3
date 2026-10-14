"""One Gemini SDK boundary with safe operational diagnostics."""

import asyncio
import json
import logging
from time import monotonic
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.services.ai_request_security import current_request_id

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class ProviderFailure(Exception):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def readiness() -> dict:
    key = settings.GEMINI_API_KEY.strip()
    configured = bool(key and not key.startswith("your_"))
    return {
        "enabled": settings.GEMINI_ENABLED,
        "semantic_enabled": settings.GEMINI_SEMANTIC_ENABLED,
        "public_search_enabled": settings.GEMINI_PUBLIC_SEARCH_ENABLED,
        "key_configured": configured,
        "model": settings.GEMINI_MODEL_NAME,
        "backend": "google-genai/Gemini Developer API",
        "environment": settings.ENVIRONMENT,
        "state": "READY_TO_CHECK" if settings.GEMINI_ENABLED and configured else "DISABLED" if not settings.GEMINI_ENABLED else "KEY_MISSING",
    }


def record(operation: str, outcome: str, elapsed_ms: int = 0, category: str | None = None) -> None:
    emit = logger.warning if outcome in {"CALLED_FAILED", "OUTPUT_REJECTED"} else logger.info
    emit(json.dumps({
        "event": "ai_provider", "request_id": current_request_id(), "operation": operation,
        "model": settings.GEMINI_MODEL_NAME, "elapsed_ms": elapsed_ms,
        "outcome": outcome, "category": category,
    }))


def classify_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "PROVIDER_TIMEOUT"
    if isinstance(exc, (OSError, ConnectionError)):
        return "NETWORK_ERROR"
    if isinstance(exc, errors.APIError):
        code = exc.code if isinstance(exc.code, int) else 0
        if code in {401, 403}:
            return "AUTHENTICATION_FAILED"
        if code == 404:
            return "MODEL_UNAVAILABLE"
        if code == 429:
            return "QUOTA_EXCEEDED"
        if code in {408, 504}:
            return "PROVIDER_TIMEOUT"
        return "NETWORK_ERROR" if code >= 500 else "INVALID_PROVIDER_RESPONSE"
    if isinstance(exc, ValidationError):
        return "SCHEMA_VALIDATION_FAILED"
    return "INVALID_PROVIDER_RESPONSE"


async def generate_json(operation: str, prompt: str, instruction: str, schema: type[T],
                        *, timeout: float, max_tokens: int) -> T:
    state = readiness()
    if state["state"] != "READY_TO_CHECK":
        record(operation, "SKIPPED_DISABLED", category="PROVIDER_DISABLED")
        raise ProviderFailure("PROVIDER_DISABLED")
    started = monotonic()
    try:
        async with genai.Client(api_key=settings.GEMINI_API_KEY.strip()).aio as client:
            response = await asyncio.wait_for(client.models.generate_content(
                model=settings.GEMINI_MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    temperature=0,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    # Preserve JSON Schema keys such as additionalProperties;
                    # the SDK's OpenAPI Schema path can send additional_properties
                    # for extra="forbid", which the Developer API rejects.
                    response_json_schema=schema.model_json_schema(),
                ),
            ), timeout=timeout)
        if not response.text:
            raise ProviderFailure("INVALID_PROVIDER_RESPONSE")
        try:
            result = schema.model_validate_json(response.text)
        except ValidationError as exc:
            raise ProviderFailure("SCHEMA_VALIDATION_FAILED") from exc
        record(operation, "CALLED_SUCCESS", int((monotonic() - started) * 1000))
        return result
    except ProviderFailure as exc:
        record(operation, "OUTPUT_REJECTED", int((monotonic() - started) * 1000), exc.category)
        raise
    except Exception as exc:
        category = classify_error(exc)
        record(operation, "CALLED_FAILED", int((monotonic() - started) * 1000), category)
        raise ProviderFailure(category) from exc


async def generate_search_answer(prompt: str, instruction: str):
    """Native Google Search grounding; citations are validated by the caller."""
    if readiness()["state"] != "READY_TO_CHECK" or not settings.GEMINI_PUBLIC_SEARCH_ENABLED:
        raise ProviderFailure("PROVIDER_DISABLED")
    started = monotonic()
    try:
        async with genai.Client(api_key=settings.GEMINI_API_KEY.strip()).aio as client:
            response = await asyncio.wait_for(client.models.generate_content(
                model=settings.GEMINI_MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    temperature=0,
                    max_output_tokens=2048,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            ), timeout=settings.GEMINI_PUBLIC_SEARCH_TIMEOUT_SECONDS)
        record("public_policy_search", "CALLED_SUCCESS", int((monotonic() - started) * 1000))
        return response
    except Exception as exc:
        category = classify_error(exc)
        record("public_policy_search", "CALLED_FAILED", int((monotonic() - started) * 1000), category)
        raise ProviderFailure(category) from exc
