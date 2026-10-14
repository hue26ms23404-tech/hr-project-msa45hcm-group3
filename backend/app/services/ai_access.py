"""Access decisions derived exclusively from authenticated server-side roles."""
from fastapi import HTTPException, status
from app.core.authorization import ROLE_LEVEL, authenticated_roles, capabilities, known_roles, require_capability

def user_roles(user) -> set[str]:
    return authenticated_roles(user)


def require_known_role(roles: set[str]) -> None:
    """Reject unknown or missing roles instead of silently granting self access."""
    known_roles(roles)


def require_reviewer_role(roles: set[str]) -> None:
    """Report creation permission; NOT permission to approve HR requests."""
    require_capability(roles, "report.create")


def require_approver_role(roles: set[str]) -> None:
    """Only a manager or higher can enter a business approval workflow."""
    if not known_roles(roles) & {"MANAGER", "HR", "ADMIN"}:
        raise HTTPException(status_code=403, detail="PERMISSION_DENIED")


def allowed_knowledge_roles(roles: set[str]) -> list[str]:
    require_capability(roles, "knowledge.read")
    level = max(ROLE_LEVEL[role] for role in known_roles(roles))
    return [role for role, required in ROLE_LEVEL.items() if required <= level]


def report_scope(user, requested_scope: str | None = None) -> str:
    """Resolve and authorize a report scope using roles from the authenticated user."""
    roles = user_roles(user)
    require_reviewer_role(roles)

    actor_caps = capabilities(roles)
    allowed = {"SELF"}
    if "report.read_direct_reports" in actor_caps:
        allowed.add("DIRECT_REPORTS")
    if "report.read_company" in actor_caps:
        allowed.add("COMPANY")
    default = ("COMPANY" if "COMPANY" in allowed else
               "DIRECT_REPORTS" if "DIRECT_REPORTS" in allowed else "SELF")

    selected = requested_scope or default
    if selected not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="AI_ACCESS_DENIED")
    if selected in {"SELF", "DIRECT_REPORTS"} and getattr(user, "employee_id", None) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="AI_ACCESS_DENIED")
    return selected
