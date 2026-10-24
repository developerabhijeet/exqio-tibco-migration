"""where a build's generated service runs: the worker's internal address and its name

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('builds', sa.Column('run_host', sa.Text(), nullable=True), schema='api')
    op.add_column('builds', sa.Column('run_owner', sa.Text(), nullable=True), schema='api')


def downgrade() -> None:
    op.drop_column('builds', 'run_owner', schema='api')
    op.drop_column('builds', 'run_host', schema='api')
