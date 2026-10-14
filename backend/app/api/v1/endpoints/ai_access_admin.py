"""Admin-only controls for the AI's maximum DB data scopes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.ai_role_scope import AIRoleScopePolicy
from app.services.ai_role_scopes import MAX_SCOPES, load_ai_scopes, require_ai_admin

router = APIRouter()


class ScopeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(min_length=1, max_length=3)


@router.get("/roles")
async def list_role_scopes(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    require_ai_admin(user)
    current = await load_ai_scopes(db)
    return [{"role": role, "scopes": sorted(current[role]), "maximum": sorted(maximum),
             "editable": role != "ADMIN"} for role, maximum in MAX_SCOPES.items()]


@router.put("/roles/{role_code}")
async def update_role_scopes(role_code: str, body: ScopeUpdate,
                             db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    require_ai_admin(user)
    if role_code not in MAX_SCOPES or role_code == "ADMIN":
        raise HTTPException(422, "AI_ROLE_POLICY_NOT_EDITABLE")
    scopes = set(body.scopes)
    if len(scopes) != len(body.scopes) or "SELF" not in scopes or not scopes.issubset(MAX_SCOPES[role_code]):
        raise HTTPException(422, "AI_ROLE_SCOPE_INVALID")
    row = await db.scalar(select(AIRoleScopePolicy).where(AIRoleScopePolicy.role_code == role_code).with_for_update())
    if row is None:
        row = AIRoleScopePolicy(role_code=role_code)
        db.add(row)
    row.allow_direct_reports = "DIRECT_REPORTS" in scopes
    row.allow_company = "COMPANY" in scopes
    row.updated_by_user_account_id = user.user_account_id
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return {"role": role_code, "scopes": sorted(scopes), "maximum": sorted(MAX_SCOPES[role_code]),
            "editable": True}
