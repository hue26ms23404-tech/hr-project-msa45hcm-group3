from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.schemas.reports import ReportRunCreate
from app.services import report_run_service as module
from app.services.report_service import ReportService


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "storage", "empty_scope"])
@pytest.mark.parametrize("employee_id", [None, 7])
async def test_run_freezes_scope_and_persists_terminal_state(monkeypatch, failure, employee_id):
    events = []
    actor = SimpleNamespace(user_account_id=9, login_email="owner@example.test", role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="MANAGER"))])
    req = ReportRunCreate(kind="ATTENDANCE", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31), employee_id=employee_id)
    ids = [] if failure == "empty_scope" else [7]
    visible = AsyncMock(return_value=("SELF", ids))
    monkeypatch.setattr(ReportService, "visible_employee_ids", visible)

    async def save(user, request, run_id, scope, employee_ids, status, **values):
        assert user is actor and request is req and employee_ids == ids
        events.append((status, values))
        return SimpleNamespace(status=status, scope=scope)

    async def generate(*args, **kwargs):
        assert events[0][0] == "GENERATING"
        assert kwargs["source_employee_ids"] == ids
        if failure == "empty_scope":
            raise HTTPException(403, "AI_ACCESS_DENIED")
        return {"rows": [{"employee_id": 7}], "kind": "ATTENDANCE"}

    monkeypatch.setattr(module.ReportRunService, "_save_state", save)
    monkeypatch.setattr(ReportService, "generate", generate)
    monkeypatch.setattr(module, "build_xlsx_report", Mock(return_value=b"xlsx"))
    stored = Mock(side_effect=[("workbook.xlsx", "digest"), OSError("private path")] if failure == "storage" else [("workbook.xlsx", "digest"), ("snapshot.json", "digest")])
    monkeypatch.setattr(module, "store_report", stored)
    deleted = Mock()
    monkeypatch.setattr(module, "delete_report", deleted)
    if failure:
        with pytest.raises(HTTPException) as caught:
            await module.ReportRunService._create(AsyncMock(), actor, req)
        assert caught.value.status_code == (503 if failure == "storage" else 403)
        assert [event[0] for event in events] == ["GENERATING", "FAILED"]
        assert events[-1][1]["error_code"] == ("DATA_SERVICE_UNAVAILABLE" if failure == "storage" else "REPORT_REQUEST_REJECTED")
        assert events[-1][1]["storage_key"] is None
        if failure == "storage":
            deleted.assert_any_call("workbook.xlsx")
    else:
        result = await module.ReportRunService._create(AsyncMock(), actor, req)
        assert result["run"].status == "READY"
        assert [event[0] for event in events] == ["GENERATING", "READY"]
        assert events[-1][1]["snapshot_storage_key"] == "snapshot.json"
    assert visible.call_args.kwargs == ({"employee_id": employee_id} if employee_id else {})
    filters = module.ReportRunService.request_filters(req)
    assert filters.get("employee_id") == employee_id


@pytest.mark.asyncio
async def test_frozen_ids_narrow_existing_manager_query():
    from sqlalchemy.dialects import postgresql
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    actor = SimpleNamespace(employee_id=7, role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="MANAGER"))])
    await ReportService.generate(db, actor, "HEADCOUNT", date(2026, 10, 1), date(2026, 10, 31), None, source_employee_ids=[8])
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "manager_employee_id = 7" in sql and "employee_id IN (8)" in sql

@pytest.mark.asyncio
@pytest.mark.parametrize("previous_scope,requested_scope", [
    ("COMPANY", "SELF"),
    ("SELF", "COMPANY"),
    ("DIRECT_REPORTS", "SELF"),
])
async def test_report_draft_replay_rejects_changed_scope(monkeypatch, previous_scope, requested_scope):
    actor = SimpleNamespace(
        user_account_id=9, employee_id=7, login_email="owner@example.test",
        role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="HR"))],
    )
    req = ReportRunCreate(
        kind="ATTENDANCE", start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31), scope=requested_scope,
    )
    previous = SimpleNamespace(
        status="READY", kind=req.kind, scope=previous_scope,
        filters={"start_date": "2026-10-01", "end_date": "2026-10-31", "department_id": None},
    )
    state_db = AsyncMock()
    state_db.__aenter__.return_value = state_db
    state_db.scalar.return_value = previous
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: state_db)
    monkeypatch.setattr(ReportService, "visible_employee_ids",
                        AsyncMock(side_effect=AssertionError("Do not create a second report")))
    with pytest.raises(HTTPException) as caught:
        await module.ReportRunService._create(AsyncMock(), actor, req, source_draft_id="draft-1")
    assert caught.value.status_code == 409
    assert caught.value.detail == "REPORT_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_report_draft_replay_reuses_matching_scope(monkeypatch):
    from app.services import report_access

    actor = SimpleNamespace(
        user_account_id=9, employee_id=7, login_email="owner@example.test",
        role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="HR"))],
    )
    req = ReportRunCreate(
        kind="ATTENDANCE", start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31), scope="COMPANY",
    )
    previous = SimpleNamespace(
        status="READY", kind=req.kind, scope="COMPANY",
        filters={"start_date": "2026-10-01", "end_date": "2026-10-31", "department_id": None},
    )
    state_db = AsyncMock()
    state_db.__aenter__.return_value = state_db
    state_db.scalar.return_value = previous
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: state_db)
    authorize = AsyncMock(return_value=previous)
    monkeypatch.setattr(report_access, "authorized_run", authorize)

    result = await module.ReportRunService._create(AsyncMock(), actor, req, source_draft_id="draft-1")
    assert result == {"run": previous, "report": None}
    authorize.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_target,new_target", [(23, 24), (23, None), (None, 23)])
async def test_report_replay_rejects_changed_employee_filter(monkeypatch, previous_target, new_target):
    actor = SimpleNamespace(user_account_id=9, employee_id=7,
                            role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="HR"))])
    req = ReportRunCreate(kind="ATTENDANCE", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
                          scope="COMPANY", employee_id=new_target)
    filters = {"start_date": "2026-10-01", "end_date": "2026-10-31", "department_id": None}
    if previous_target is not None:
        filters["employee_id"] = previous_target
    previous = SimpleNamespace(status="READY", kind="ATTENDANCE", scope="COMPANY", filters=filters)
    state_db = AsyncMock()
    state_db.__aenter__.return_value = state_db
    state_db.scalar.return_value = previous
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: state_db)
    with pytest.raises(HTTPException) as caught:
        await module.ReportRunService._create(AsyncMock(), actor, req, source_draft_id="draft-1")
    assert caught.value.status_code == 409
