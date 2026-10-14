"""Server-enforced AI data-scope restrictions configured by administrators."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_user
from app.core.authorization import ai_scope_context, assigned_roles
from app.core.database import get_db
from app.models.ai_role_scope import AIRoleScopePolicy

# Role maxima are fixed independently from the editable AI configuration.
MAX_SCOPES = {
    "EMPLOYEE": {"SELF"},
    "MANAGER": {"SELF", "DIRECT_REPORTS"},
    "HR": {"SELF", "DIRECT_REPORTS", "COMPANY"},
    "ADMIN": {"SELF", "DIRECT_REPORTS", "COMPANY"},
}


def defaults():
    return {role: set(scopes) for role, scopes in MAX_SCOPES.items()}


async def load_ai_scopes(db):
    limits = defaults()
    try:
        result = await db.execute(select(AIRoleScopePolicy))
        rows = result.scalars().all()
    except (SQLAlchemyError, TimeoutError) as exc:
        # Configuration outages must never silently revert to broad defaults.
        raise HTTPException(503, "DATA_SERVICE_UNAVAILABLE") from exc
    for row in rows:
        if row.role_code not in MAX_SCOPES or row.role_code == "ADMIN":
            continue
        limits[row.role_code] = {"SELF"}
        if row.allow_direct_reports and "DIRECT_REPORTS" in MAX_SCOPES[row.role_code]:
            limits[row.role_code].add("DIRECT_REPORTS")
        if row.allow_company and "COMPANY" in MAX_SCOPES[row.role_code]:
            limits[row.role_code].add("COMPANY")
    return limits


async def scoped_ai_access(db=Depends(get_db), user=Depends(get_current_user)):
    # Fail closed if configuration cannot be loaded (including missing migration).
    with ai_scope_context(await load_ai_scopes(db)):
        yield


def require_ai_admin(user):
    if "ADMIN" not in assigned_roles(user):
        raise HTTPException(status_code=403, detail="PERMISSION_DENIED")
