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
