"""Admin-managed AI data scopes; these cannot promote a user's HR role."""
from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f70819"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("hr_ai_role_scope_policies",
        sa.Column("role_code", sa.String(length=30), primary_key=True),
        sa.Column("allow_direct_reports", sa.Boolean(), nullable=False),
        sa.Column("allow_company", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_account_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role_code IN ('EMPLOYEE','MANAGER','HR')", name="ck_ai_scope_policy_role"))


def downgrade():
    op.drop_table("hr_ai_role_scope_policies")
