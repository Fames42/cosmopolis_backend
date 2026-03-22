"""add managing_ticket to conversationstateenum

Revision ID: 004
Revises: 003
Create Date: 2026-03-22

"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE conversationstateenum ADD VALUE IF NOT EXISTS 'managing_ticket'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op
    pass
