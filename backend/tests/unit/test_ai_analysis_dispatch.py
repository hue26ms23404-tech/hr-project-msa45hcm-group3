from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.schemas.ai import AIChatRequest
from app.services.ai_service import AIService
from app.services.ai_analysis_tool import AIAnalysisTool


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,valid", [
    ('{"intent":"ANALYZE_REPORT"}', True),
    ('{"intent":"ANALYZE_REPORT","python":"print(1)"}', False),
    ('{"intent":"EXECUTE_SQL"}', False),
])
async def test_gemini_analysis_contract(monkeypatch, payload, valid):
    from app.services import ai_service as module
    from app.ai.providers.gemini import ProviderFailure
    from app.schemas.ai import AIIntentResult
    from pydantic import ValidationError
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    async def fake_generate(*args, **kwargs):
        try:
            return AIIntentResult.model_validate_json(payload)
        except ValidationError as exc:
            raise ProviderFailure("SCHEMA_VALIDATION_FAILED") from exc
    model = AsyncMock(side_effect=fake_generate)
    monkeypatch.setattr(module, "generate_json", model)
    if valid:
        assert await AIService._classify_free_intent("phân tích báo cáo") == "ANALYZE_REPORT"
    else:
        with pytest.raises(HTTPException) as error:
            await AIService._classify_free_intent("phân tích báo cáo")
        assert error.value.status_code == 503
    assert model.call_args.args[3] is AIIntentResult
    assert model.call_args.kwargs["max_tokens"] == 512


@pytest.mark.asyncio
async def test_analysis_followup_uses_authenticated_actor_and_tool(monkeypatch):
    tool = AsyncMock(return_value={"row_count": 2, "metrics": {}})
    monkeypatch.setattr(AIAnalysisTool, "execute", tool)
    actor = SimpleNamespace(user_account_id=9)
    request = AIChatRequest(message="phân tích báo cáo này", report_run_id="a" * 32)
    result = await AIService.process_chat(None, 7, request, {"HR"}, 9, actor)
    tool.assert_awaited_once_with(None, actor, "a" * 32)
    assert "2 dòng" in result.reply


@pytest.mark.asyncio
async def test_analysis_rejects_missing_actor_before_tool(monkeypatch):
    tool = AsyncMock()
    monkeypatch.setattr(AIAnalysisTool, "execute", tool)
    with pytest.raises(HTTPException) as error:
        await AIService.process_chat(None, 7, AIChatRequest(message="phân tích báo cáo này", report_run_id="a" * 32), {"EMPLOYEE"})
    assert error.value.status_code == 403
    tool.assert_not_called()


@pytest.mark.asyncio
async def test_analysis_action_is_auditable_without_user_text(monkeypatch):
    from app.services import ai_request_security as security
    db = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock())
    session = AsyncMock()
    session.__aenter__.return_value = db
    monkeypatch.setattr(security, "AsyncSessionLocal", lambda: session)
    await security.record_event(None, 9, "REPORT", "REPORT_ANALYSIS", "SELF", "SUCCESS", "analysis-test", "ATTENDANCE")
    event = db.add.call_args.args[0]
    assert event.action == "REPORT_ANALYSIS" and event.command == "ATTENDANCE"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_followup_dispatches_gemini_intent_to_tool(monkeypatch):
    from app.services import ai_service as module
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    classifier = AsyncMock(return_value="ANALYZE_REPORT")
    monkeypatch.setattr(AIService, "_classify_free_intent", classifier)
    tool = AsyncMock(return_value={"row_count": 4, "metrics": {}})
    monkeypatch.setattr(AIAnalysisTool, "execute", tool)
    actor = SimpleNamespace(user_account_id=9)
    request = AIChatRequest(message="nhận xét dữ liệu của báo cáo này", report_run_id="a" * 32)
    result = await AIService.process_chat(None, 7, request, {"HR"}, 9, actor)
    classifier.assert_awaited_once_with(request.message)
    tool.assert_awaited_once_with(None, actor, "a" * 32)
    assert "4 dòng" in result.reply
