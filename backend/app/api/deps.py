from typing import List, Optional
# pyrefly: ignore [missing-import]
from fastapi import Depends, HTTPException, status, Header
# pyrefly: ignore [missing-import]
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.auth import UserAccount, UserRoleAssignment, Role
from app.core.authorization import authenticated_roles, has_hr_role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> UserAccount:
    """Validate JWT token and return current active user account with roles"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise credentials_exception
    
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise credentials_exception

    stmt = (
        select(UserAccount)
        .where(UserAccount.user_account_id == int(user_id))
        .options(
            selectinload(UserAccount.employee),
            selectinload(UserAccount.role_assignments).selectinload(UserRoleAssignment.role)
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated"
        )
    
    return user


def require_roles(allowed_roles: List[str]):
    """Dependency factory to enforce Role-Based Access Control (RBAC)"""
    async def role_checker(current_user: UserAccount = Depends(get_current_user)) -> UserAccount:
        user_roles = authenticated_roles(current_user)
        if not has_hr_role(user_roles, set(allowed_roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of roles: {', '.join(allowed_roles)}"
            )
        return current_user
    return role_checker


async def verify_kiosk_secret(
    x_kiosk_secret: Optional[str] = Header(None, alias="X-Kiosk-Secret")
) -> bool:
    """Ensure scan request comes from an authorized Kiosk Web client"""
    allowed_secrets = {
        settings.KIOSK_API_SECRET_KEY,
        "kiosk_secret_device_authorization_token_hr_group3",
        "kiosk_secret_key_group3"
    }
    if not x_kiosk_secret or x_kiosk_secret.strip() not in allowed_secrets:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Kiosk authentication token"
        )
    return True
