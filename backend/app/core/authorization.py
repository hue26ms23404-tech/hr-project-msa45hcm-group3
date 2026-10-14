"""Capabilities from authenticated HR role assignments; resource scope is checked later."""

from fastapi import HTTPException
from contextvars import ContextVar
from contextlib import contextmanager

ROLE_LEVEL = {"EMPLOYEE": 0, "MANAGER": 1, "HR": 2, "ADMIN": 3}
KNOWN_ROLES = frozenset(ROLE_LEVEL)
# Request-scoped AI restrictions. Outside AI chat tools, HR RBAC is unchanged.
_ai_scope_limits = ContextVar("hr_ai_scope_limits", default=None)


@contextmanager
def ai_scope_context(limits):
    token = _ai_scope_limits.set(limits)
    try:
        yield
    finally:
        _ai_scope_limits.reset(token)


ROLE_CAPABILITIES = {
    "EMPLOYEE": frozenset({"leave.read_self", "leave.draft_self", "attendance.read_self", "attendance.draft_fix_self", "knowledge.read", "report.create", "report.read_self", "report.analyze"}),
    "MANAGER": frozenset({"leave.read_self", "leave.draft_self", "attendance.read_self", "attendance.draft_fix_self", "knowledge.read", "report.create", "report.headcount", "report.read_self", "report.read_direct_reports", "report.analyze"}),
    "HR": frozenset({"leave.read_self", "leave.draft_self", "attendance.read_self", "attendance.draft_fix_self", "knowledge.read", "knowledge.manage", "report.create", "report.headcount", "report.read_self", "report.read_direct_reports", "report.read_company", "report.analyze"}),
    "ADMIN": frozenset({"leave.read_self", "leave.draft_self", "attendance.read_self", "attendance.draft_fix_self", "knowledge.read", "knowledge.manage", "report.create", "report.headcount", "report.read_self", "report.read_direct_reports", "report.read_company", "report.analyze"}),
}


def assigned_roles(user) -> set[str]:
    return {assignment.role.role_code for assignment in getattr(user, "role_assignments", ())
            if getattr(getattr(assignment, "role", None), "role_code", None)}


authenticated_roles = assigned_roles


def known_roles(roles: set[str]) -> set[str]:
    known = roles & ROLE_CAPABILITIES.keys()
    if not known:
        raise HTTPException(403, "AI_ACCESS_DENIED")
    return known


def capabilities(roles: set[str]) -> frozenset[str]:
    roles = known_roles(roles)
    effective = set().union(*(ROLE_CAPABILITIES[role] for role in roles))
    limits = _ai_scope_limits.get()
    if limits is not None and "ADMIN" not in roles:
        scopes = set().union(*(limits.get(role, {"SELF"}) for role in roles))
        if "DIRECT_REPORTS" not in scopes:
            effective.discard("report.read_direct_reports")
        if "COMPANY" not in scopes:
            effective.discard("report.read_company")
    return frozenset(effective)


def require_capability(roles: set[str], capability: str) -> None:
    if capability not in capabilities(roles):
        raise HTTPException(403, "AI_ACCESS_DENIED")


def has_hr_role(roles: set[str], allowed_roles: set[str]) -> bool:
    return bool("ADMIN" in roles or known_roles(roles) & allowed_roles)
