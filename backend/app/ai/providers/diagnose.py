"""Opt-in, sanitized live provider check: python -m app.ai.providers.diagnose --live."""

import argparse
import asyncio
import json

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict

from app.ai.providers.gemini import classify_error, generate_json, readiness, ProviderFailure
from app.core.config import settings


class ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


PROBE_GENERATION_TIMEOUT_SECONDS = 15


async def run_live() -> dict:
    result = {"configuration": readiness(), "initialization": "SKIPPED",
              "authentication_and_model_access": "SKIPPED", "text_generation": "SKIPPED",
              "structured_generation": "SKIPPED"}
    if result["configuration"]["state"] != "READY_TO_CHECK":
        return result
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY.strip())
        result["initialization"] = "SUCCESS"
    except Exception as exc:
        result["initialization"] = classify_error(exc)
        return result
    try:
        async with client.aio as api:
            try:
                await asyncio.wait_for(api.models.get(model=settings.GEMINI_MODEL_NAME), timeout=5)
                result["authentication_and_model_access"] = "SUCCESS"
            except Exception as exc:
                result["authentication_and_model_access"] = classify_error(exc)
                return result
            try:
                answer = await asyncio.wait_for(api.models.generate_content(
                    model=settings.GEMINI_MODEL_NAME, contents="Reply with OK.",
                    config=types.GenerateContentConfig(max_output_tokens=16, temperature=0)),
                    timeout=PROBE_GENERATION_TIMEOUT_SECONDS)
                result["text_generation"] = "SUCCESS" if answer.text and answer.text.strip() else "INVALID_PROVIDER_RESPONSE"
            except Exception as exc:
                result["text_generation"] = classify_error(exc)
    except Exception as exc:
        result["text_generation"] = classify_error(exc)
    if result["text_generation"] != "SUCCESS":
        return result
    try:
        answer = await generate_json("diagnostic_structured", "Return ok=true.",
                                     "Return JSON with boolean ok=true.", ProbeAnswer,
                                     timeout=PROBE_GENERATION_TIMEOUT_SECONDS, max_tokens=32)
        result["structured_generation"] = "SUCCESS" if answer.ok else "INVALID_PROVIDER_RESPONSE"
    except ProviderFailure as exc:
        result["structured_generation"] = exc.category
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Make bounded, billable API calls")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_live()) if args.live else {"configuration": readiness(), "live_calls": "SKIPPED"}))


if __name__ == "__main__":
    main()
