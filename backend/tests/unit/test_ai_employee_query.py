import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.authorization import ai_scope_context
from app.schemas.ai import AIChatRequest, AIChatResponse
from app.schemas.reports import ReportRunCreate
from app.services import ai_employee_query as module
from app.services.ai_employee_query import EmployeeQuery
from app.services.ai_report_draft_service import AIReportDraftService
from app.services.ai_service import AIService
from app.services.report_service import ReportService


TODAY = date(2026, 10, 14)
TARGET = SimpleNamespace(employee_id=23, employee_code="EMP023", full_name="Nguyễn Văn An")


def actor(role="HR"):
    return SimpleNamespace(user_account_id=9, employee_id=7,
                          role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code=role))])


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "HR muốn xem số ngày phép của nhân viên EMP023",
    "hr muon xem so ngay phep cua nhan vien EMP023",
    "EMP023 còn bao nhiêu ngày phép năm 2026?",
])
async def test_hr_reads_named_employee_balance_not_own_balance(monkeypatch, message):
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", False)
    monkeypatch.setattr("app.services.ai_service.business_today", lambda: TODAY)
    db = AsyncMock()
    db.execute.side_effect = [SimpleNamespace(all=lambda: [TARGET]),
                             SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(remaining_days=Decimal("7.50")))]
    result = await AIService.process_chat(db, 7, AIChatRequest(message=message), {"HR"}, 9, actor())
    assert "EMP023" in result.reply and "7.50" in result.reply
    assert result.action is None  # Do not navigate to the actor's own leave page.
    assert "employee_id = 23" in sql(db.execute.call_args.args[0])
    assert "hr_employee_leave_balances.year = 2026" in sql(db.execute.call_args.args[0])
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_target_report_starts_draft_with_employee_filter(monkeypatch):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [TARGET])
    start = AsyncMock(return_value=AIChatResponse(reply="report draft"))
    monkeypatch.setattr(AIReportDraftService, "start", start)
    request = AIChatRequest(message="Xem báo cáo chấm công của nhân viên EMP023 tháng trước")
    owner = actor()
    result = await AIService.process_chat(db, 7, request, {"HR"}, 9, owner)
    assert "report draft" in result.reply and "EMP023" in result.reply
    params = start.call_args.args[4]
    assert params == {"kind": "ATTENDANCE", "scope": "COMPANY", "employee_id": 23}
    assert start.call_args.args[1] is owner


@pytest.mark.asyncio
@pytest.mark.parametrize("role,limited", [("EMPLOYEE", False), ("HR", True)])
async def test_denied_scope_never_resolves_employee_or_calls_gemini(monkeypatch, role, limited):
    interpret = AsyncMock(side_effect=AssertionError("No provider for denied scope"))
    monkeypatch.setattr(module, "interpret_employee_query", interpret)
    monkeypatch.setattr(module.AIKnowledgeTool, "sources", AsyncMock(return_value=[]))
    db = AsyncMock()
    with ai_scope_context({"HR": {"SELF"}} if limited else None):
        result = await AIService.process_chat(db, 7, AIChatRequest(message="Xem số dư phép của EMP023"), {role}, 9, actor(role))
    assert result.answer_mode == "OUT_OF_SCOPE" and result.action is None
    interpret.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope", [("HR", "COMPANY"), ("MANAGER", "DIRECT_REPORTS")])
async def test_name_resolution_is_scoped_before_data_lookup(role, scope):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [])
    await module.resolve_employee(db, actor(role), scope, "Nguyen Van An")
    statement = sql(db.execute.call_args.args[0])
    assert "translate(lower(hr_employees.full_name)" in statement
    assert "nguyen van an" in statement and "LIMIT 6" in statement
    assert ("manager_employee_id = 7" in statement) == (role == "MANAGER")
    assert len(module._ACCENTS) == len(module._LETTERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("employees,code", [([], "EMPLOYEE_NOT_FOUND"), ([TARGET, TARGET], "EMPLOYEE_AMBIGUOUS")])
async def test_unknown_or_duplicate_target_asks_without_reading_balance(monkeypatch, employees, code):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: employees)
    result = await module.answer_employee_query(db, actor(), AIChatRequest(message="Số dư phép của EMP023"), TODAY)
    assert result.message_code == code and result.action is None
    assert db.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Nguyễn Văn An còn được nghỉ mấy ngày?", "Nguyễn Văn An còn bao nhiêu ngày phép?"])
async def test_name_first_request_reaches_gemini_before_knowledge_retrieval(monkeypatch, message):
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    generate = AsyncMock(return_value=EmployeeQuery(intent="LEAVE_BALANCE", employee_reference="Nguyễn Văn An"))
    monkeypatch.setattr(module, "generate_json", generate)
    docs = AsyncMock(side_effect=AssertionError("Target request must not be answered as policy"))
    monkeypatch.setattr(AIService, "_knowledge_documents", docs)
    db = AsyncMock()
    db.execute.side_effect = [SimpleNamespace(all=lambda: [TARGET]),
                             SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(remaining_days=Decimal("3.00")))]
    result = await AIService.process_chat(db, 7, AIChatRequest(message=message), {"HR"}, 9, actor())
    assert "EMP023" in result.reply and "3.00" in result.reply
    payload = json.loads(generate.call_args.args[1])
    assert payload == {"message": message}
    assert generate.call_args.args[3] is EmployeeQuery
    docs.assert_not_called()


@pytest.mark.asyncio
async def test_model_cannot_invent_target(monkeypatch):
    monkeypatch.setattr(module, "interpret_employee_query", AsyncMock(return_value=EmployeeQuery(
        intent="LEAVE_BALANCE", employee_reference="EMP999")))
    db = AsyncMock()
    result = await module.answer_employee_query(db, actor(), AIChatRequest(message="Số dư phép của EMP023"), TODAY)
    assert result.status == "NEEDS_INPUT" and result.action is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_keeps_target_out_of_personal_fallback(monkeypatch):
    from app.ai.providers.gemini import ProviderFailure
    monkeypatch.setattr(module, "interpret_employee_query", AsyncMock(side_effect=ProviderFailure("PROVIDER_TIMEOUT")))
    db = AsyncMock()
    result = await module.answer_employee_query(db, actor(), AIChatRequest(message="Số dư phép của Nguyễn Văn An"), TODAY)
    assert result.message_code == "EMPLOYEE_QUERY_UNAVAILABLE" and result.action is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Số dư phép của EMP023 và EMP024", "Xem số dư phép và báo cáo chấm công EMP023", "Xóa số dư phép EMP023"])
async def test_multiple_or_unsupported_actions_do_not_execute(message):
    db = AsyncMock()
    result = await module.answer_employee_query(db, actor(), AIChatRequest(message=message), TODAY)
    assert result.status == "NEEDS_INPUT" and result.action is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope", [("EMPLOYEE", "SELF"), ("MANAGER", "DIRECT_REPORTS"), ("HR", "COMPANY")])
async def test_report_employee_filter_intersects_authenticated_scope(role, scope):
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [23])
    result = await ReportService.visible_employee_ids(db, actor(role), "ATTENDANCE", scope, None, employee_id=23)
    statement = sql(db.scalars.call_args.args[0])
    assert "hr_employees.employee_id = 23" in statement
    if role == "EMPLOYEE":
        assert "hr_employees.employee_id = 7" in statement
    if role == "MANAGER":
        assert "manager_employee_id = 7" in statement
    assert result == (scope, [23])


@pytest.mark.asyncio
async def test_report_employee_outside_scope_fails_instead_of_widening():
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [])
    with pytest.raises(HTTPException) as caught:
        await ReportService.visible_employee_ids(db, actor("MANAGER"), "ATTENDANCE", "DIRECT_REPORTS", None, employee_id=23)
    assert caught.value.status_code == 404


@pytest.mark.parametrize("target", [0, -1, True, "23"])
def test_report_target_id_is_strict_positive_integer(target):
    with pytest.raises(ValidationError):
        ReportRunCreate(kind="ATTENDANCE", start_date=TODAY, end_date=TODAY, employee_id=target)


def test_target_filter_does_not_change_payroll_contract():
    with pytest.raises(ValidationError):
        ReportRunCreate(kind="MY_PAYSLIP", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31), employee_id=23)
