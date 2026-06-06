"""add ticket reminder state

Revision ID: 010
Revises: 009
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("reminder_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "reminder_state")
