from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.core.authorization import ai_scope_context, capabilities
from app.services.ai_role_scopes import MAX_SCOPES, load_ai_scopes, require_ai_admin
from app.api.v1.endpoints.ai_access_admin import ScopeUpdate, update_role_scopes


def actor(*roles):
    return SimpleNamespace(user_account_id=8, employee_id=12, role_assignments=[
        SimpleNamespace(role=SimpleNamespace(role_code=role)) for role in roles
    ])


@pytest.mark.asyncio
async def test_role_policy_read_is_restrictive_and_does_not_escape_request():
    db = AsyncMock()
    rows = [
        SimpleNamespace(role_code="MANAGER", allow_direct_reports=False, allow_company=False),
        SimpleNamespace(role_code="HR", allow_direct_reports=True, allow_company=False),
    ]
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    scopes = await load_ai_scopes(db)
    assert scopes["EMPLOYEE"] == {"SELF"}
    assert scopes["MANAGER"] == {"SELF"}
    assert scopes["HR"] == {"SELF", "DIRECT_REPORTS"}
    assert scopes["ADMIN"] == MAX_SCOPES["ADMIN"]
    with ai_scope_context(scopes):
        assert "report.read_direct_reports" not in capabilities({"MANAGER"})
        assert "report.read_company" not in capabilities({"HR"})
        assert "report.read_company" in capabilities({"ADMIN"})
    assert "report.read_direct_reports" in capabilities({"MANAGER"})
    assert "report.read_company" in capabilities({"HR"})


@pytest.mark.asyncio
async def test_admin_can_restrict_manager_but_not_grant_company():
    db = AsyncMock()
    db.add = Mock()
    db.scalar.return_value = None
    result = await update_role_scopes("MANAGER", ScopeUpdate(scopes=["SELF"]), db, actor("ADMIN"))
    assert result["scopes"] == ["SELF"]
    db.add.assert_called_once()
    created = db.add.call_args.args[0]
    assert created.role_code == "MANAGER" and not created.allow_direct_reports and not created.allow_company
    assert created.updated_by_user_account_id == 8
    db.flush.assert_awaited_once()

    for role, scopes in (("EMPLOYEE", ["SELF", "COMPANY"]),
                         ("MANAGER", ["SELF", "COMPANY"]),
                         ("ADMIN", ["SELF"])):
        with pytest.raises(HTTPException) as exc:
            await update_role_scopes(role, ScopeUpdate(scopes=scopes), db, actor("ADMIN"))
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_cannot_change_or_list_policy():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await update_role_scopes("HR", ScopeUpdate(scopes=["SELF"]), db, actor("HR"))
    assert exc.value.status_code == 403
    db.scalar.assert_not_awaited()
    with pytest.raises(HTTPException):
        require_ai_admin(actor("MANAGER"))


@pytest.mark.asyncio
async def test_role_policy_database_outage_fails_closed():
    from sqlalchemy.exc import SQLAlchemyError
    db = AsyncMock()
    db.execute.side_effect = SQLAlchemyError("Database unavailable")
    with pytest.raises(HTTPException) as exc:
        await load_ai_scopes(db)
    assert exc.value.status_code == 503
    assert exc.value.detail == "DATA_SERVICE_UNAVAILABLE"
