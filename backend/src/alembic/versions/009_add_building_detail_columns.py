"""add detail columns to buildings

Revision ID: 009
Revises: 008
Create Date: 2026-03-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("buildings", sa.Column("house_number", sa.String(), nullable=True))
    op.add_column("buildings", sa.Column("legal_number", sa.String(), nullable=True))
    op.add_column("buildings", sa.Column("floor", sa.String(), nullable=True))
    op.add_column("buildings", sa.Column("block", sa.String(), nullable=True))
    op.add_column("buildings", sa.Column("actual_number", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("buildings", "actual_number")
    op.drop_column("buildings", "block")
    op.drop_column("buildings", "floor")
    op.drop_column("buildings", "legal_number")
    op.drop_column("buildings", "house_number")
