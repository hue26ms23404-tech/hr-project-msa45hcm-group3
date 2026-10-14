"""Administrative scope limits on AI access, separate from HR assignments."""
from datetime import datetime
from sqlalchemy import String, Boolean, BigInteger, DateTime, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.core.database import Base


class AIRoleScopePolicy(Base):
    __tablename__ = "hr_ai_role_scope_policies"
    role_code: Mapped[str] = mapped_column(String(30), primary_key=True)
    allow_direct_reports: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_company: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by_user_account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("role_code IN ('EMPLOYEE','MANAGER','HR')",
                                     name="ck_ai_scope_policy_role"),)
