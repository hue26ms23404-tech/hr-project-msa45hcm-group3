from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.authorization import assigned_roles, capabilities, has_hr_role, require_capability
from app.services.ai_access import allowed_knowledge_roles, report_scope


def actor(*roles, employee_id=7):
    return SimpleNamespace(employee_id=employee_id, role_assignments=[
        SimpleNamespace(role=SimpleNamespace(role_code=role)) for role in roles], roles=["ADMIN"])


@pytest.mark.parametrize("roles,expected_company", [
    (("ADMIN",), True), (("ADMIN", "HR"), True),
    (("ADMIN", "EXPERIMENTAL"), True), (("MANAGER",), False),
    (("EMPLOYEE",), False),
])
def test_capabilities_come_from_known_hr_assignments(roles, expected_company):
    user = actor(*roles)
    observed = assigned_roles(user)
    assert ("report.read_company" in capabilities(observed)) is expected_company
    assert has_hr_role(observed, {"HR"}) is expected_company
    if expected_company:
        assert report_scope(user, "COMPANY") == "COMPANY"
    else:
        with pytest.raises(HTTPException):
            report_scope(user, "COMPANY")


@pytest.mark.parametrize("roles", [("UNKNOWN",), ()])
def test_unknown_only_and_no_assignment_are_denied(roles):
    observed = assigned_roles(actor(*roles))
    with pytest.raises(HTTPException) as error:
        require_capability(observed, "knowledge.read")
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        allowed_knowledge_roles(observed)


def test_forged_client_role_does_not_change_server_role():
    user = actor("EMPLOYEE")
    assert assigned_roles(user) == {"EMPLOYEE"}
    assert "report.read_company" not in capabilities(assigned_roles(user))


def test_ai_scope_restrictions_cannot_increase_hr_permissions():
    from app.core.authorization import ai_scope_context
    actor_roles = {"MANAGER"}
    limits = {
        "EMPLOYEE": {"SELF"},
        "MANAGER": {"SELF"},
        "HR": {"SELF", "COMPANY"},
        "ADMIN": {"SELF", "DIRECT_REPORTS", "COMPANY"},
    }
    with ai_scope_context(limits):
        assert "report.read_direct_reports" not in capabilities(actor_roles)
        assert "report.read_company" not in capabilities(actor_roles)
        with pytest.raises(HTTPException):
            report_scope(actor("MANAGER"), "DIRECT_REPORTS")
        assert report_scope(actor("MANAGER"), "SELF") == "SELF"
        with pytest.raises(HTTPException):
            report_scope(actor("HR"), "DIRECT_REPORTS")
        assert report_scope(actor("HR"), "COMPANY") == "COMPANY"
        assert report_scope(actor("ADMIN"), "COMPANY") == "COMPANY"
    assert "report.read_direct_reports" in capabilities(actor_roles)


def test_admin_role_policy_never_grants_company_to_employee():
    from app.core.authorization import ai_scope_context
    # Even a malformed database row cannot grant capabilities above role maxima.
    with ai_scope_context({"EMPLOYEE": {"SELF", "DIRECT_REPORTS", "COMPANY"}}):
        assert "report.read_company" not in capabilities({"EMPLOYEE"})
        assert "report.read_direct_reports" not in capabilities({"EMPLOYEE"})
        with pytest.raises(HTTPException):
            report_scope(actor("EMPLOYEE"), "COMPANY")
