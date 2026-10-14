from datetime import date
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.v1.endpoints import reports
from app.core.database import get_db
from app.models.ai_security import AIRequestEvent
from app.services.ai_suggestions import resolve_suggestion
from app.services.ai_service import AIService
from app.services.ai_request_security import ACTIONS
from app.services.report_catalog import report_catalog
from app.services.report_service import ReportService
from app.services.report_access import authorized_run
from app.schemas.ai import AIChatRequest


def actor(*roles):
    return SimpleNamespace(employee_id=7, user_account_id=9,
        role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code=role)) for role in roles])


@pytest.mark.parametrize("roles,scopes", [
    (("EMPLOYEE",), ["SELF"]), (("MANAGER",), ["SELF", "DIRECT_REPORTS"]),
    (("HR",), ["SELF", "DIRECT_REPORTS", "COMPANY"]),
    (("ADMIN",), ["SELF", "DIRECT_REPORTS", "COMPANY"]),
    (("EMPLOYEE", "HR"), ["SELF", "DIRECT_REPORTS", "COMPANY"]),
])
def test_report_catalog_role_matrix(roles, scopes):
    catalog = report_catalog(actor(*roles))["reports"]
    if not scopes:
        assert catalog == []
    else:
        attendance = next(item for item in catalog if item["kind"] == "ATTENDANCE")
        assert attendance["scopes"] == scopes
        assert any(item["kind"] == "PAYROLL_SUMMARY" for item in catalog) == bool(set(roles) & {"HR", "ADMIN"})


@pytest.mark.parametrize("suggestion", ["headcount_report", "team_attendance_report", "team_leave_report", "company_attendance_report", "payroll_summary"])
def test_employee_report_suggestions_are_denied(suggestion):
    with pytest.raises(HTTPException) as denied:
        resolve_suggestion(suggestion, {"EMPLOYEE"}, 7)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tạo báo cáo công toàn công ty tháng này", "Tạo báo cáo tổng hợp lương toàn công ty tháng trước"])
async def test_employee_free_report_prompts_denied_before_db(message):
    db = AsyncMock()
    with pytest.raises(HTTPException) as denied:
        await AIService.process_chat(db, 7, AIChatRequest(message=message), {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert denied.value.status_code == 403
    db.execute.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["APPROVAL_QUEUE", "HEADCOUNT", "PAYROLL_SUMMARY"])
async def test_employee_all_report_services_denied_before_query(kind):
    db = AsyncMock()
    for call in (
        ReportService.generate(db, actor("EMPLOYEE"), kind, date(2026, 10, 1), date(2026, 10, 31), None, "SELF"),
        ReportService.visible_employee_ids(db, actor("EMPLOYEE"), kind, "SELF", None),
    ):
        with pytest.raises(HTTPException) as denied:
            await call
        assert denied.value.status_code == 403
    db.execute.assert_not_called()
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_employee_can_only_discover_self_reports():
    app = FastAPI()
    app.include_router(reports.router, prefix="/reports")
    db = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: actor("EMPLOYEE")
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/reports/catalog")
    assert response.status_code == 200
    entries = response.json()["reports"]
    assert set(row["kind"] for row in entries) == {"ATTENDANCE", "LEAVE", "ATTENDANCE_FIX", "MY_PAYSLIP"}
    assert all(row["scopes"] == ["SELF"] for row in entries)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_employee_cannot_access_another_account_report_snapshot():
    db = AsyncMock()
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as denied:
        await authorized_run(db, actor("EMPLOYEE"), "a" * 32)
    assert denied.value.status_code == 404
    db.scalar.assert_awaited_once()


def test_employee_self_report_suggestions_but_no_company_access():
    from app.services.ai_access import report_scope
    for name in ["my_attendance_report", "my_payslip"]:
        resolve_suggestion(name, {"EMPLOYEE"}, 7)
    assert report_scope(actor("EMPLOYEE"), "SELF") == "SELF"
    with pytest.raises(HTTPException):
        report_scope(actor("EMPLOYEE"), "DIRECT_REPORTS")
    with pytest.raises(HTTPException):
        report_scope(actor("EMPLOYEE"), "COMPANY")


def test_audit_schema_accepts_every_service_action():
    constraint = next(item for item in AIRequestEvent.__table__.constraints if item.name == "ck_ai_request_event_action")
    assert set(re.findall(r"'([A-Z_]+)'", str(constraint.sqltext))) == set(ACTIONS)


@pytest.mark.parametrize("roles,employee_id,expected", [
    ({"EMPLOYEE"}, 7, False),
    ({"MANAGER"}, 7, True),
    ({"HR"}, 7, True),
    ({"ADMIN"}, 7, True),
    ({"HR"}, None, True),
    ({"MANAGER"}, None, False),
])
def test_headcount_suggestion_and_report_catalog_share_eligibility(roles, employee_id, expected):
    from app.services.ai_suggestions import suggestion_allowed
    user = actor(*roles)
    user.employee_id = employee_id
    available_kinds = {item["kind"] for item in report_catalog(user)["reports"]}
    assert suggestion_allowed("headcount_report", roles, employee_id) is expected
    assert ("HEADCOUNT" in available_kinds) is expected
    if expected:
        resolve_suggestion("headcount_report", roles, employee_id)
    else:
        with pytest.raises(HTTPException) as exc:
            resolve_suggestion("headcount_report", roles, employee_id)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_employee_headcount_suggestion_rejected_before_business_queries():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await AIService.process_chat(db, 7,
            AIChatRequest(suggestion_id="headcount_report"),
            {"EMPLOYEE"}, 9, actor("EMPLOYEE"))
    assert exc.value.status_code == 403
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
