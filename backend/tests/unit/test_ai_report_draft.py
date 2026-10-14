from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.services.ai_draft_service import AIDraftService
from app.services.ai_report_draft_service import AIReportDraftService, report_answers
from app.services.report_run_service import ReportRunService


def actor(role="MANAGER"):
    return SimpleNamespace(user_account_id=9, employee_id=7, role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code=role))])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, role, message, scope", [
    ("MY_PAYSLIP", "MANAGER", "Xem bảng lương của tôi tháng này", "SELF"),
    ("PAYROLL_SUMMARY", "HR", "Xem bảng lương toàn công ty tháng này", "COMPANY"),
    ("ATTENDANCE", "MANAGER", "Xem công của tôi tháng này", "SELF"),
])
async def test_dispatch_kind_controls_current_month_dates(monkeypatch, kind, role, message, scope):
    from app.services import ai_report_draft_service as module
    db = AsyncMock()
    db.add = Mock()
    interpreter = AsyncMock(return_value={})
    create = AsyncMock(return_value={"run": SimpleNamespace(run_id="a" * 32)})
    monkeypatch.setattr(module, "interpret_report_prompt", interpreter)
    monkeypatch.setattr(ReportRunService, "create", create)
    monkeypatch.setattr(AIDraftService, "mark_ready", AsyncMock())
    user = actor(role)
    initial = {"kind": kind}
    today = date(2026, 10, 9)

    await AIReportDraftService.start(db, user, message, today, initial)

    interpreter.assert_awaited_once_with(message, today, user, initial)
    req = create.await_args.args[2]
    assert req.kind == kind and req.scope == scope
    assert req.start_date == date(2026, 10, 1)
    assert req.end_date == date(2026, 10, 9 if kind == "ATTENDANCE" else 31)

@pytest.mark.asyncio
async def test_report_collects_period_then_authorized_scope_before_real_run(monkeypatch):
    db = AsyncMock()
    db.add = Mock()
    user = actor()
    create = AsyncMock(return_value={"run": SimpleNamespace(run_id="a" * 32)})
    monkeypatch.setattr(ReportRunService, "create", create)
    monkeypatch.setattr(AIDraftService, "mark_ready", AsyncMock())
    result = await AIReportDraftService.start(db, user, "Tạo báo cáo công", date(2026, 10, 7))
    draft = db.add.call_args.args[0]
    assert result.missing_fields == ["start_date", "end_date", "scope"]
    create.assert_not_called()

    async def update(_db, draft_id, owner_id, params, missing):
        assert draft_id == draft.draft_id and owner_id == 9
        draft.typed_params = params
        draft.missing_fields = missing
        draft.revision += 1
        return draft

    monkeypatch.setattr(AIDraftService, "update_draft", update)
    result = await AIReportDraftService.continue_draft(db, user, draft, "Tháng này", date(2026, 10, 7))
    assert result.missing_fields == ["scope"]
    assert {item.value for item in result.input_options} == {"SELF", "DIRECT_REPORTS"}
    create.assert_not_called()
    result = await AIReportDraftService.continue_draft(db, user, draft, "Bản thân", date(2026, 10, 7), {"scope": "SELF"})
    assert result.action.data["run_id"] == "a" * 32
    req = create.await_args.args[2]
    assert req.start_date == date(2026, 10, 1) and req.end_date == date(2026, 10, 7) and req.scope == "SELF"


@pytest.mark.asyncio
async def test_unresolved_report_followup_preserves_revision(monkeypatch):
    from app.services import ai_report_draft_service as module
    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000", revision=2, status="COLLECTING",
                            typed_params={"kind": "ATTENDANCE"}, missing_fields=["start_date", "end_date", "scope"])
    monkeypatch.setattr(module, "interpret_report_prompt", AsyncMock(return_value={}))
    update = AsyncMock()
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    result = await AIReportDraftService.continue_draft(AsyncMock(), actor(), draft, "chưa rõ", date(2026, 10, 13))
    assert result.draft_revision == 2 and result.missing_fields == draft.missing_fields
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_scope_escalation_rejected_before_persistence():
    db = AsyncMock()
    with pytest.raises(HTTPException) as denied:
        await AIReportDraftService.start(db, actor("EMPLOYEE"), "Tạo báo cáo công toàn công ty tháng này", date(2026, 10, 7))
    assert denied.value.status_code == 403
    db.add.assert_not_called()


def test_report_fields_and_month_parser_are_closed():
    assert report_answers("Tạo báo cáo phiếu lương của tôi tháng này", date(2026, 10, 7))["end_date"] == "2026-10-31"
    assert report_answers("tháng 9/2026", date(2026, 10, 7))["end_date"] == "2026-09-30"
    with pytest.raises(HTTPException):
        AIDraftService._validate_command_and_params("DRAFT_REPORT", {"employee_ids": "all"})
    with pytest.raises(HTTPException):
        AIDraftService._validate_command_and_params("DRAFT_REPORT", {"department_id": True})
    with pytest.raises(HTTPException):
        AIReportDraftService.validate_access(actor("EMPLOYEE"), {"kind": "ATTENDANCE", "scope": "SELF"})
    with pytest.raises(HTTPException):
        AIReportDraftService.validate_access(actor("EMPLOYEE"), {"kind": "PAYROLL_SUMMARY"})


@pytest.mark.asyncio
async def test_common_report_period_is_parsed_without_provider(monkeypatch):
    from app.services import ai_report_interpreter as module
    model = Mock(side_effect=AssertionError("Common filters must stay local"))
    monkeypatch.setattr(module, "generate_json", model)
    assert await module.GeminiReportInterpreter().interpret("báo cáo công tháng trước", date(2026, 10, 8), actor()) == {}
    model.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [
    ('{"start_date":"2026-09-01","end_date":"2026-09-30"}', {"start_date":"2026-09-01","end_date":"2026-09-30"}),
    ('{"start_date":"2026-02-31"}', {}),
    ('{"employee_id":99}', {}),
    ('{"kind":"RAW_SQL"}', {}),
])
async def test_report_provider_has_closed_filter_contract(monkeypatch, payload, expected):
    from app.services import ai_report_interpreter as module
    from app.ai.providers.gemini import ProviderFailure
    from pydantic import ValidationError
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    async def fake_generate(*args, **kwargs):
        try:
            return module.ReportPromptResult.model_validate_json(payload)
        except ValidationError as exc:
            raise ProviderFailure("SCHEMA_VALIDATION_FAILED") from exc
    model = AsyncMock(side_effect=fake_generate)
    monkeypatch.setattr(module, "generate_json", model)
    result = await module.GeminiReportInterpreter().interpret("lấy kỳ trước đó", date(2026, 10, 8), actor(),
        {"kind":"ATTENDANCE","scope":"SELF","employee_id":99})
    assert result == expected
    prompt = model.call_args.args[1]
    assert "employee_id" not in prompt and "user_account_id" not in prompt
    assert model.call_args.kwargs["timeout"] == 5


@pytest.mark.asyncio
async def test_model_cannot_grant_company_scope(monkeypatch):
    from app.services import ai_report_draft_service as module
    monkeypatch.setattr(module, "interpret_report_prompt", AsyncMock(return_value={
        "kind":"ATTENDANCE","scope":"COMPANY","start_date":"2026-10-01","end_date":"2026-10-08"}))
    db = AsyncMock(); db.add = Mock()
    with pytest.raises(HTTPException) as exc:
        await AIReportDraftService.start(db, actor("EMPLOYEE"), "some report", date(2026,10,8))
    assert exc.value.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_report_revision_is_owner_bound_and_reuses_only_saved_filters(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services import ai_report_draft_service as module
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(kind="ATTENDANCE", scope="SELF",
        status="READY", expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
        filters={"start_date":"2026-10-01","end_date":"2026-10-08","department_id":None})
    start = AsyncMock()
    monkeypatch.setattr(AIReportDraftService, "start", start)
    monkeypatch.setattr(module, "interpret_report_prompt", AsyncMock(return_value={}))
    await AIReportDraftService.revise_run(db, actor(), "a"*32, "đổi sang tháng trước", date(2026,10,8))
    params = start.await_args.args[4]
    assert params == {"kind":"ATTENDANCE","scope":"SELF","start_date":"2026-09-01","end_date":"2026-09-30"}
    from sqlalchemy.dialects import postgresql
    sql = str(db.scalar.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds":True}))
    assert "owner_user_account_id = 9" in sql
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        await AIReportDraftService.revise_run(db, actor(), "a"*32, "tháng trước", date(2026,10,8))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("today, expected_start, expected_end", [
    (date(2026,10,8),"2026-11-01","2026-11-30"),
    (date(2026,12,8),"2027-01-01","2027-01-31"),
    (date(2028,1,31),"2028-02-01","2028-02-29"),
])
@pytest.mark.parametrize("message", ["đổi sang tháng sau", "đổi sang tháng tới"])
def test_next_month_filters_use_hcm_today_and_calendar_boundaries(today, expected_start, expected_end, message):
    assert report_answers(message,today,{"kind":"ATTENDANCE","start_date":"2026-09-01","end_date":"2026-09-30"}) == {
        "start_date":expected_start,"end_date":expected_end}


@pytest.mark.asyncio
async def test_next_month_needs_no_provider_and_revision_keeps_scope(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services import ai_report_interpreter as module
    provider = Mock(side_effect=AssertionError("Month aliases must run locally"))
    monkeypatch.setattr(module,"generate_json",provider)
    assert await module.GeminiReportInterpreter().interpret("đổi sang tháng sau",date(2026,10,8),actor()) == {}
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(kind="ATTENDANCE",scope="SELF",status="READY",
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
        filters={"start_date":"2026-09-01","end_date":"2026-09-30","department_id":None})
    start = AsyncMock()
    monkeypatch.setattr(AIReportDraftService,"start",start)
    await AIReportDraftService.revise_run(db,actor(),"a"*32,"đổi sang tháng sau",date(2026,10,8))
    assert start.await_args.args[4] == {"kind":"ATTENDANCE","scope":"SELF","start_date":"2026-11-01","end_date":"2026-11-30"}
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_completed_report_draft_replay_never_creates_second_report(monkeypatch):
    from app.services import ai_report_draft_service as module
    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000",
        revision=5, status="READY", missing_fields=[],
        typed_params={"kind": "ATTENDANCE", "scope": "SELF",
                      "start_date": "2026-10-01", "end_date": "2026-10-14"})
    create = AsyncMock(side_effect=AssertionError("Never persist a completed report twice"))
    interpreter = AsyncMock(return_value={})
    monkeypatch.setattr(ReportRunService, "create", create)
    monkeypatch.setattr(module, "interpret_report_prompt", interpreter)
    with pytest.raises(HTTPException) as e:
        await AIReportDraftService.continue_draft(AsyncMock(), actor(), draft, "không đổi", date(2026, 10, 14))
    assert e.value.status_code == 409
    create.assert_not_awaited()
    interpreter.assert_not_awaited()
    with pytest.raises(HTTPException):
        await AIReportDraftService.finish_or_ask(AsyncMock(), actor(), draft)
    create.assert_not_awaited()
