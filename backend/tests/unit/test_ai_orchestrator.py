from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.ai import tool_registry
from app.schemas.ai import AIChatRequest, AIChatResponse


def context(roles, employee_id=7):
    return tool_registry.ToolContext(None, employee_id, 9, roles,
                                     AIChatRequest(message="tra cứu phép"), date(2026, 10, 13))


@pytest.mark.asyncio
async def test_registry_executes_only_known_authorized_intents(monkeypatch):
    original = tool_registry.TOOLS["LEAVE_BALANCE"]
    handler = AsyncMock(return_value=AIChatResponse(reply="ok"))
    monkeypatch.setitem(tool_registry.TOOLS, "LEAVE_BALANCE",
                        tool_registry.RegisteredTool(original.tool_id, original.input_schema,
                                                     original.capability, original.needs_employee, handler))
    assert await tool_registry.execute_registered("EXECUTE_SQL", context({"ADMIN"})) is None
    with pytest.raises(HTTPException):
        await tool_registry.execute_registered("LEAVE_BALANCE", context({"UNKNOWN"}))
    with pytest.raises(HTTPException):
        await tool_registry.execute_registered("LEAVE_BALANCE", context({"EMPLOYEE"}, None))
    result = await tool_registry.execute_registered("LEAVE_BALANCE", context({"ADMIN", "UNKNOWN"}))
    assert result.reply == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_question_during_draft_preserves_reason_and_revision(monkeypatch):
    from types import SimpleNamespace
    from app.ai.orchestrator import ChatOrchestrator
    from app.services.ai_service import AIService
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(draft_id="a" * 36, revision=3,
        status="COLLECTING", command="DRAFT_LEAVE", missing_fields=["reason"])
    get_draft = AsyncMock(return_value=draft)
    lookup = AsyncMock(return_value=AIChatResponse(reply="Chính sách phép năm hiện hành."))
    update = AsyncMock(side_effect=AssertionError("Do not mutate draft reason"))
    monkeypatch.setattr(AIDraftService, "get_draft", get_draft)
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    monkeypatch.setattr(AIService, "_answer_knowledge", lookup)
    response = await ChatOrchestrator.process_chat(
        None, 7, AIChatRequest(message="Quy định nghỉ phép thế nào?",
            draft_id=draft.draft_id, draft_revision=3),
        {"EMPLOYEE"}, 9)
    assert response.reply == "Chính sách phép năm hiện hành."
    assert response.draft_id == draft.draft_id
    assert response.draft_revision == 3
    assert response.missing_fields == ["reason"]
    assert response.status == "NEEDS_INPUT"
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_switching_from_leave_draft_to_report_requires_explicit_cancel(monkeypatch):
    from types import SimpleNamespace
    from app.ai.orchestrator import ChatOrchestrator
    from app.services.ai_service import AIService
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(draft_id="b" * 36, revision=2,
        command="DRAFT_LEAVE", missing_fields=["reason"])
    monkeypatch.setattr(AIDraftService, "get_draft", AsyncMock(return_value=draft))
    continuation = AsyncMock(side_effect=AssertionError("Do not consume new report request as reason"))
    monkeypatch.setattr(AIService, "_continue_draft", continuation)
    response = await ChatOrchestrator.process_chat(
        None, 7, AIChatRequest(message="Tạo báo cáo chấm công tháng này",
             draft_id=draft.draft_id, draft_revision=2),
        {"EMPLOYEE"}, 9)
    assert response.message_code == "DRAFT_SWITCH_CONFIRMATION_REQUIRED"
    assert response.draft_id == draft.draft_id
    continuation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("message,inputs", [
    ("Lý do: tôi muốn dùng số dư phép còn lại", None),
    ("Lý do tôi muốn dùng số dư phép còn lại", None),
    ("Vì tôi muốn dùng số dư phép còn lại", None),
    ("Bởi vì tôi muốn dùng số dư phép còn lại", None),
    ("Do tôi muốn dùng số dư phép còn lại", None),
    ("ly do: toi muon dung so du phep con lai", None),
    ("Cả ngày vì tôi muốn dùng số dư phép còn lại", None),
    ("Vì tôi cần tìm hiểu chính sách nghỉ phép", None),
    ("Vì tôi cần tạo báo cáo chấm công", None),
    ("Cho tôi xem số dư phép còn lại", {"reason": "Việc gia đình"}),
])
async def test_explicit_draft_reason_not_routed_to_balance_lookup(monkeypatch, message, inputs):
    from app.ai import orchestrator
    from app.ai.orchestrator import ChatOrchestrator
    from app.services.ai_service import AIService
    from app.services.ai_draft_service import AIDraftService

    continuation = AsyncMock(return_value=AIChatResponse(reply="Đã ghi nhận lý do."))
    monkeypatch.setattr(AIService, "_continue_draft", continuation)
    monkeypatch.setattr(AIDraftService, "get_draft",
                        AsyncMock(side_effect=AssertionError("Do not interrupt explicit draft input")))
    monkeypatch.setattr(orchestrator, "execute_registered",
                        AsyncMock(side_effect=AssertionError("Do not look up leave balance")))
    draft_id = "c" * 36
    result = await ChatOrchestrator.process_chat(
        None, 7, AIChatRequest(
            message=message, draft_id=draft_id, draft_revision=3, inputs=inputs
        ), {"EMPLOYEE"}, 9,
    )
    assert result.reply == "Đã ghi nhận lý do."
    continuation.assert_awaited_once()
    assert continuation.await_args.args[2:5] == (draft_id, message, 7)
    assert continuation.await_args.args[6] == inputs
