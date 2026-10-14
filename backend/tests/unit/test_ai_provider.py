import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from app.ai.providers import gemini


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


def test_provider_failure_diagnostic_is_visible_at_warning_level(caplog):
    caplog.set_level(logging.WARNING)
    gemini.record("interpret_chat_request", "CALLED_FAILED", category="PROVIDER_TIMEOUT")
    assert "PROVIDER_TIMEOUT" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


class FakeAsyncClient:
    def __init__(self, response):
        self.models = SimpleNamespace(generate_content=AsyncMock(return_value=response))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


@pytest.mark.asyncio
async def test_structured_provider_uses_new_sdk_and_validates_output(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(gemini.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "test-key")
    client = FakeAsyncClient(SimpleNamespace(text='{"ok":true}'))
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: SimpleNamespace(aio=client))
    assert (await gemini.generate_json("test", "safe prompt", "safe instruction", Answer,
                                       timeout=2, max_tokens=32)).ok
    call = client.models.generate_content.call_args
    assert call.kwargs["config"].response_schema is None
    assert call.kwargs["config"].response_json_schema == Answer.model_json_schema()
    assert call.kwargs["config"].response_json_schema["additionalProperties"] is False
    assert call.kwargs["config"].max_output_tokens == 32
    assert call.kwargs["model"] == gemini.settings.GEMINI_MODEL_NAME
    assert "test-key" not in caplog.text
    assert "CALLED_SUCCESS" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("response,category", [
    (SimpleNamespace(text='{"ok":"not a boolean"}'), "SCHEMA_VALIDATION_FAILED"),
    (SimpleNamespace(text='{"ok":true,"employee_id":99}'), "SCHEMA_VALIDATION_FAILED"),
    (SimpleNamespace(text=""), "INVALID_PROVIDER_RESPONSE"),
])
async def test_invalid_provider_output_is_categorized(monkeypatch, response, category):
    monkeypatch.setattr(gemini.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "test-key")
    client = FakeAsyncClient(response)
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: SimpleNamespace(aio=client))
    with pytest.raises(gemini.ProviderFailure) as error:
        await gemini.generate_json("test", "prompt", "instruction", Answer, timeout=2, max_tokens=32)
    assert error.value.category == category


@pytest.mark.asyncio
async def test_disabled_provider_makes_no_client(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_ENABLED", False)
    client = AsyncMock()
    monkeypatch.setattr(gemini.genai, "Client", client)
    with pytest.raises(gemini.ProviderFailure) as error:
        await gemini.generate_json("test", "prompt", "instruction", Answer, timeout=2, max_tokens=32)
    assert error.value.category == "PROVIDER_DISABLED"
    client.assert_not_called()


@pytest.mark.asyncio
async def test_provider_timeout_is_not_success(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(gemini.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "test-key")
    client = FakeAsyncClient(None)
    client.models.generate_content.side_effect = asyncio.TimeoutError()
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: SimpleNamespace(aio=client))
    with pytest.raises(gemini.ProviderFailure) as error:
        await gemini.generate_json("test", "prompt", "instruction", Answer, timeout=2, max_tokens=32)
    assert error.value.category == "PROVIDER_TIMEOUT"
    assert "CALLED_FAILED" in caplog.text


@pytest.mark.asyncio
async def test_public_search_uses_native_tool_without_json_schema(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_PUBLIC_SEARCH_ENABLED", True)
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "test-key")
    response = SimpleNamespace(text="grounded text")
    client = FakeAsyncClient(response)
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: SimpleNamespace(aio=client))
    assert await gemini.generate_search_answer("public question", "instruction") is response
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.tools[0].google_search is not None
    assert config.response_json_schema is None and config.response_schema is None
