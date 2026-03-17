"""drop unique constraint on tenants.phone, change agent_enabled default to false

Revision ID: 002
Revises: 001
Create Date: 2026-03-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the unique index on phone
    op.drop_index("ix_tenants_phone", table_name="tenants")
    # Recreate as a non-unique index
    op.create_index("ix_tenants_phone", "tenants", ["phone"], unique=False)
    # Change agent_enabled default from true to false
    op.alter_column("tenants", "agent_enabled", server_default=sa.text("false"))


def downgrade() -> None:
    op.alter_column("tenants", "agent_enabled", server_default=sa.text("true"))
    op.drop_index("ix_tenants_phone", table_name="tenants")
    op.create_index("ix_tenants_phone", "tenants", ["phone"], unique=True)
