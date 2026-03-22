"""rename move_in_date to lease_start_date and lease_duration to lease_end_date

Revision ID: 005
Revises: 004
Create Date: 2026-03-22

"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tenants", "move_in_date", new_column_name="lease_start_date")
    op.alter_column("tenants", "lease_duration", new_column_name="lease_end_date")


def downgrade() -> None:
    op.alter_column("tenants", "lease_start_date", new_column_name="move_in_date")
    op.alter_column("tenants", "lease_end_date", new_column_name="lease_duration")
