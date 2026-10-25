"""memory events and delayed job retry: ``memory_events``; ``jobs.not_before``

``not_before`` keeps a requeued job (``RetryLater``) from being claimed before its delay is over;
the claim reads ``(status, not_before, created_at)``. ``memory_events`` holds the decisions and
curations the memory graph ingests (the reason travels in ``context``).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('not_before', sa.DateTime(timezone=True), nullable=True), schema='api')
    op.create_index(op.f('ix_jobs_status_not_before_created_at'), 'jobs', ['status', 'not_before', 'created_at'],
                    unique=False, schema='api')
    op.create_table(
        'memory_events',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('project_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column('target_uid', sa.Text(), nullable=True),
        sa.Column('replacement_uid', sa.Text(), nullable=True),
        sa.Column('actor', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind IN ('decision','curation')", name=op.f('ck_memory_events_kind')),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'], name=op.f('fk_memory_events_project_id_projects'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_memory_events')),
        schema='api',
    )
    op.create_index(op.f('ix_memory_events_project_id_created_at'), 'memory_events', ['project_id', 'created_at'],
                    unique=False, schema='api')


def downgrade() -> None:
    op.drop_index('ix_memory_events_project_id_created_at', table_name='memory_events', schema='api')
    op.drop_table('memory_events', schema='api')
    op.drop_index('ix_jobs_status_not_before_created_at', table_name='jobs', schema='api')
    op.drop_column('jobs', 'not_before', schema='api')
