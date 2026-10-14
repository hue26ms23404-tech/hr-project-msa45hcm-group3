from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.ai import AIChatRequest
from app.services.ai_service import AIService


@pytest.mark.asyncio
async def test_unrecognized_hr_question_prompts_clarification_without_provider(monkeypatch):
    monkeypatch.setattr(AIService, "_knowledge_documents", AsyncMock(return_value=[]))
    classify = AsyncMock(side_effect=HTTPException(status_code=503, detail="AI_UNAVAILABLE"))
    monkeypatch.setattr(AIService, "_classify_free_intent", classify)
    db = AsyncMock()
    answer = await AIService._answer_knowledge(
        db, {"EMPLOYEE"}, date(2026, 10, 14),
        AIChatRequest(message="Cho tôi xem thông tin nhân sự của mình"),
        employee_id=7, owner_user_account_id=9,
    )
    assert answer.message_code == "CLARIFICATION_REQUIRED"
    assert answer.status == "NEEDS_INPUT"
    assert "được cấp quyền" in answer.reply
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_non_hr_request_asks_instead_of_running_any_tool(monkeypatch):
    monkeypatch.setattr(AIService, "_knowledge_documents", AsyncMock(return_value=[]))
    classify = AsyncMock(side_effect=AssertionError("No model call should be required"))
    monkeypatch.setattr(AIService, "_classify_free_intent", classify)
    answer = await AIService._answer_knowledge(
        AsyncMock(), {"EMPLOYEE"}, date(2026, 10, 14),
        AIChatRequest(message="Giúp tôi với"),
        employee_id=7, owner_user_account_id=9,
    )
    assert answer.message_code == "CLARIFICATION_REQUIRED"
    classify.assert_not_awaited()
