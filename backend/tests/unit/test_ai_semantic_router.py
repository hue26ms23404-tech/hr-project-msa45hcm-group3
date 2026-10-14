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
@pytest.mark.parametrize("category", ["PROVIDER_TIMEOUT", "QUOTA_EXCEEDED", "SCHEMA_VALIDATION_FAILED", "NETWORK_ERROR"])
async def test_provider_failure_does_not_fall_through_to_self_handler(monkeypatch, category):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(side_effect=ProviderFailure(category)))
    personal = AsyncMock(side_effect=AssertionError("No regex fallback may substitute self"))
    monkeypatch.setattr(AIEmployeeTool, "get_context", personal)
    response = await AIService.process_chat(AsyncMock(), 7, AIChatRequest(message="A còn bao nhiêu ngày phép?"), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert response.status == "UNAVAILABLE" and response.message_code == f"AI_INTENT_{category}"
    assert "thử lại sau" in response.reply
    assert not any(word in response.reply.lower() for word in ("gemini", "google", "hạn mức", "quota", "schema", category.lower()))
    personal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target,reference", [("EMPLOYEE", None), ("SELF", "Le Thi Nhan Vien")])
async def test_incomplete_or_conflicting_reference_is_clarification_not_provider_outage(monkeypatch, target, reference):
    plan = ChatPlan(intent="LEAVE_BALANCE", target=target, employee_reference=reference)
    assert plan.target == "UNSPECIFIED"
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=plan))
    db = AsyncMock()
    result = await AIService.process_chat(db, 7, AIChatRequest(message="so ngay phep con lai cua Le Thi Nhan Vien"), {"HR"}, 9, actor("HR"))
    assert result.message_code == "TARGET_REQUIRED" and result.action is None
    db.execute.assert_not_called()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Tôi muốn biết lý do bị trừ ngày phép",
    "Tại sao bị trừ phép bởi vì đi muộn?",
    "Tôi cần biết ly do chính sách này thay đổi",
])
async def test_policy_question_is_not_truncated_as_a_draft_reason(monkeypatch, message):
    from app.ai.parsing import intent
    generate = AsyncMock(return_value=ChatPlan(intent="POLICY_QUERY", target="NONE"))
    monkeypatch.setattr(intent, "generate_json", generate)
    await intent.interpret_chat_request(message, date(2026, 10, 15))
    assert json.loads(generate.call_args.args[1])["message"] == message


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "Nguyễn Văn An còn bao nhiêu ngày phép?",
    "nguyen van an con bao nhieu ngay phep?",
    "Tôi muốn xem số dư phép của Nguyễn Văn An",
    "Show Nguyen Van An's remaining leave balance",
    "Nguyễn Văn An còn bao nhiêu ngày phép, xem cho tôi",
    "Show my employee's remaining leave balance",
    "Show the leave balance of my colleague",
])
@pytest.mark.parametrize("role", ["EMPLOYEE", "HR"])
@pytest.mark.parametrize("intent", ["LEAVE_BALANCE", "ATTENDANCE_SUMMARY", "REPORT_ATTENDANCE", "REPORT_LEAVE", "REPORT_ATTENDANCE_FIX", "REPORT_PAYSLIP", "REPORT_HEADCOUNT", "REPORT_PAYROLL", "ANALYZE_REPORT"])
async def test_wrong_self_classification_never_reads_actor_data(monkeypatch, message, role, intent):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=ChatPlan(intent=intent, target="SELF")))
    dispatch = AsyncMock(side_effect=AssertionError("Never substitute self for a named/unclear subject"))
    monkeypatch.setattr(AIService, "_dispatch_free_intent", dispatch)
    db = AsyncMock()
    result = await AIService.process_chat(db, 7, AIChatRequest(message=message), {role}, 9, actor(role))
    assert result.status == "NEEDS_INPUT" and result.action is None
    dispatch.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tôi còn bao nhiêu ngày phép?", "Tôi là nhân viên muốn xem số dư phép của tôi", "Show my leave balance"])
async def test_explicit_self_request_is_not_a_foreign_employee_lookup(monkeypatch, message):
    monkeypatch.setattr(module, "interpret_chat_request", AsyncMock(return_value=ChatPlan(intent="LEAVE_BALANCE", target="SELF")))
    dispatch = AsyncMock(return_value=AIChatResponse(reply="self result"))
    monkeypatch.setattr(AIService, "_dispatch_free_intent", dispatch)
    result = await AIService.process_chat(AsyncMock(), 7, AIChatRequest(message=message), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert result.reply == "self result"
    dispatch.assert_awaited_once()
