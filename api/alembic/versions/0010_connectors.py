"""connectors: ``connector_links``

One row per remote object a connector created for a process (branch, pull request, Confluence
page, Jira issue, Figma frame), unique per (kind, object type, process, local item), so a
re-publish updates instead of duplicating. The admin's connector settings and last test results
live in ``settings`` (keys ``connectors``, ``connectors_last_test``): no schema change there.

Revision ID: 0010
Revises: 0007
Create Date: 2026-10-26 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'connector_links',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('process_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('object_type', sa.String(), nullable=False),
        sa.Column('local_ref', sa.String(), nullable=False),
        sa.Column('remote_id', sa.String(), nullable=False),
        sa.Column('remote_url', sa.String(), nullable=False),
        sa.Column('remote_version', sa.Integer(), nullable=True),
        sa.Column('stale', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['process_id'], ['api.processes.id'],
                                name=op.f('fk_connector_links_process_id_processes'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'],
                                name=op.f('fk_connector_links_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_connector_links')),
        sa.UniqueConstraint('kind', 'object_type', 'process_id', 'local_ref',
                            name=op.f('uq_connector_links_kind_object_type_process_id_local_ref')),
        schema='api',
    )
    op.create_index(op.f('ix_connector_links_process_id'), 'connector_links', ['process_id'], unique=False,
                    schema='api')
    op.create_index(op.f('ix_connector_links_project_id'), 'connector_links', ['project_id'], unique=False,
                    schema='api')


def downgrade() -> None:
    op.drop_index(op.f('ix_connector_links_project_id'), table_name='connector_links', schema='api')
    op.drop_index(op.f('ix_connector_links_process_id'), table_name='connector_links', schema='api')
    op.drop_table('connector_links', schema='api')
