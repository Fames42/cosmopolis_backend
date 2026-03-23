"""add category column to tenants

Revision ID: 008
Revises: 007
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("category", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "category")
