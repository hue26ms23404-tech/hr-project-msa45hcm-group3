import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai import semantic_router as module
from app.ai.parsing.intent import ChatPlan
from app.ai.providers.gemini import ProviderFailure
from app.core.config import settings
from app.schemas.ai import AIChatRequest, AIChatResponse, AISource
from app.services.ai_employee_tool import AIEmployeeTool
from app.services.ai_knowledge_tool import AIKnowledgeTool
from app.services.ai_service import AIService


@pytest.fixture(autouse=True)
def semantic_enabled(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_SEMANTIC_ENABLED", True)


def actor(role):
    return SimpleNamespace(user_account_id=9, employee_id=7,
                          role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code=role))])


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "xem ngay phep con lai cua nhan vien A",
    "Nhân viên A còn bao nhiêu ngày phép?",
    "A còn được nghỉ mấy ngày, xem giúp HR nhé",
])
@pytest.mark.parametrize("role", ["EMPLOYEE", "HR"])
async def test_target_is_never_substituted_with_current_user(monkeypatch, message, role):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=ChatPlan(
        intent="LEAVE_BALANCE", target="EMPLOYEE", employee_reference="A")))
    personal = AsyncMock(side_effect=AssertionError("Never read the actor's data"))
    monkeypatch.setattr(AIEmployeeTool, "get_context", personal)
    permission_source = AISource(document_id=1, title="Phân quyền", excerpt="Phân quyền truy cập: nhân viên chỉ được xem dữ liệu bản thân.")
    sources = AsyncMock(return_value=[permission_source])
    monkeypatch.setattr(AIKnowledgeTool, "sources", sources)
    db = AsyncMock()
    response = await AIService.process_chat(db, 7, AIChatRequest(message=message), {role}, 9, actor(role))
    personal.assert_not_called()
    db.execute.assert_not_called()  # A is ambiguous; HR must supply a concrete code/name.
    if role == "EMPLOYEE":
        assert response.status == "DENIED" and response.message_code == "PERMISSION_DENIED"
        assert response.sources == [permission_source]
        sources.assert_awaited_once()
    else:
        assert response.status == "NEEDS_INPUT" and response.action is None


@pytest.mark.asyncio
async def test_hr_named_balance_resolves_real_employee_id(monkeypatch):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=ChatPlan(
        intent="LEAVE_BALANCE", target="EMPLOYEE", employee_reference="Nguyễn Văn An")))
    db = AsyncMock()
    target = SimpleNamespace(employee_id=23, employee_code="EMP023", full_name="Nguyễn Văn An")
    db.execute.side_effect = [SimpleNamespace(all=lambda: [target]),
                             SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(remaining_days=Decimal("8.00")))]
    response = await AIService.process_chat(db, 7, AIChatRequest(message="Nguyễn Văn An còn bao nhiêu ngày phép?"), {"HR"}, 9, actor("HR"))
    assert "EMP023" in response.reply and "8.00" in response.reply
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_inconsistent_self_plan_does_not_trigger_personal_lookup(monkeypatch):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=ChatPlan(intent="LEAVE_BALANCE", target="SELF")))
    tool = AsyncMock(side_effect=AssertionError("Conflicting subject must not dispatch"))
    monkeypatch.setattr(AIService, "_dispatch_free_intent", tool)
    response = await AIService.process_chat(AsyncMock(), 7, AIChatRequest(message="xem ngay phep con lai cua nhan vien A"), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert response.message_code == "TARGET_CONFLICT"
    tool.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,code", [
    (ChatPlan(intent="LEAVE_BALANCE", target="UNSPECIFIED"), "TARGET_REQUIRED"),
    (ChatPlan(intent="LEAVE_BALANCE", target="SELF", multiple_actions=True), "MULTIPLE_ACTIONS_REQUIRED"),
])
async def test_ambiguous_plan_cannot_execute_any_handler(monkeypatch, plan, code):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=plan))
    tool = AsyncMock(side_effect=AssertionError("No dispatch for ambiguity"))
    monkeypatch.setattr(AIService, "_dispatch_free_intent", tool)
    response = await AIService.process_chat(AsyncMock(), 7, AIChatRequest(message="xem phép và công"), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert response.message_code == code and response.action is None
    tool.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_does_not_fall_through_to_self_handler(monkeypatch):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(side_effect=ProviderFailure("PROVIDER_TIMEOUT")))
    personal = AsyncMock(side_effect=AssertionError("No regex fallback may substitute self"))
    monkeypatch.setattr(AIEmployeeTool, "get_context", personal)
    response = await AIService.process_chat(AsyncMock(), 7, AIChatRequest(message="A còn bao nhiêu ngày phép?"), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert response.status == "UNAVAILABLE" and response.message_code == "AI_UNAVAILABLE"
    personal.assert_not_called()


@pytest.mark.asyncio
async def test_policy_plan_runs_before_action_keywords_and_uses_semantic_queries(monkeypatch):
    queries = ["chế độ thai sản điều kiện hưởng", "nghỉ sinh con bảo hiểm xã hội"]
    planner = AsyncMock(return_value=ChatPlan(intent="POLICY_QUERY", target="NONE", retrieval_queries=queries))
    monkeypatch.setattr(module, "interpret_chat_request", planner)
    answer = AsyncMock(return_value=AIChatResponse(reply="policy result"))
    monkeypatch.setattr(AIService, "_answer_knowledge", answer)
    req = AIChatRequest(message="Giải thích điều kiện rồi hướng dẫn soạn đơn nghỉ sinh con")
    result = await AIService.process_chat(AsyncMock(), 7, req, {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert result.reply == "policy result"
    assert answer.call_args.kwargs == {"policy_question": True, "retrieval_queries": queries}
    planner.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_gets_complete_question_with_subject_but_no_contact_details(monkeypatch):
    from app.ai.parsing import intent
    generate = AsyncMock(return_value=ChatPlan(intent="LEAVE_BALANCE", target="EMPLOYEE", employee_reference="Nguyễn Văn An"))
    monkeypatch.setattr(intent, "generate_json", generate)
    message = "Xem số phép của Nguyễn Văn An ngày 01-07-2026, email an@example.test, điện thoại 0912345678"
    await intent.interpret_chat_request(message, date(2026, 10, 15))
    payload = json.loads(generate.call_args.args[1])
    assert "Nguyễn Văn An" in payload["message"] and "2026" in payload["message"]
    assert "01-07-2026" in payload["message"]
    assert "an@example.test" not in payload["message"] and "0912345678" not in payload["message"]
    assert generate.call_args.args[3] is ChatPlan
