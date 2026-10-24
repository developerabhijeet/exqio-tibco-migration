"""job ownership: claiming workers, heartbeat, cancel, attempts, affinity

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('owner', sa.Text(), nullable=True), schema='api')
    op.add_column('jobs', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True), schema='api')
    op.add_column('jobs', sa.Column('cancel_requested', sa.Boolean(), server_default=sa.false(), nullable=False), schema='api')
    op.add_column('jobs', sa.Column('attempts', sa.Integer(), server_default='0', nullable=False), schema='api')
    op.add_column('jobs', sa.Column('affinity', sa.Text(), nullable=True), schema='api')
    op.create_index('ix_jobs_status_created_at', 'jobs', ['status', 'created_at'], unique=False, schema='api')


def downgrade() -> None:
    op.drop_index('ix_jobs_status_created_at', table_name='jobs', schema='api')
    op.drop_column('jobs', 'affinity', schema='api')
    op.drop_column('jobs', 'attempts', schema='api')
    op.drop_column('jobs', 'cancel_requested', schema='api')
    op.drop_column('jobs', 'heartbeat_at', schema='api')
    op.drop_column('jobs', 'owner', schema='api')
