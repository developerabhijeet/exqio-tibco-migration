"""target stack: ``projects.target_stack`` and ``builds.stack``

Both are ``NOT NULL DEFAULT 'dotnet'``, so every existing project and build stays a .NET one.

Revision ID: 0011_target_stack
Revises: 0010
Create Date: 2026-10-26 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011_target_stack'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, column in (('projects', 'target_stack'), ('builds', 'stack')):
        op.add_column(table, sa.Column(column, sa.Text(), server_default='dotnet', nullable=False), schema='api')


def downgrade() -> None:
    op.drop_column('builds', 'stack', schema='api')
    op.drop_column('projects', 'target_stack', schema='api')
