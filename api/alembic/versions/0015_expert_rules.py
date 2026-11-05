"""expert rules: ``expert_rules`` (a project's TIBCO modernisation guidance, from CSV)

Revision ID: 0015_expert_rules
Revises: 0014_iterations
Create Date: 2026-10-27 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0015_expert_rules'
down_revision: str | None = '0014_iterations'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text('now()')


def upgrade() -> None:
    op.create_table(
        'expert_rules',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('rule_id', sa.String(), nullable=False),
        sa.Column('domain', sa.String(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('guidance', sa.Text(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('pass_criteria', sa.Text(), nullable=False),
        sa.Column('primary_agent', sa.String(), nullable=False),
        sa.Column('enforcement', sa.Text(), nullable=False),
        sa.Column('applicability', sa.String(), nullable=False),
        sa.Column('version', sa.String(), nullable=False),
        sa.Column('rule_status', sa.String(), nullable=False),
        sa.Column('owner', sa.String(), nullable=False),
        sa.Column('last_reviewed', sa.String(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('uploaded_by', sa.String(), nullable=True),
        sa.Column('applied', JSONB, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'],
                                name=op.f('fk_expert_rules_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_expert_rules')),
        sa.UniqueConstraint('project_id', 'rule_id', name=op.f('uq_expert_rules_project_id_rule_id')),
        schema='api',
    )
    op.create_index(op.f('ix_expert_rules_project_id'), 'expert_rules', ['project_id'], unique=False,
                    schema='api')


def downgrade() -> None:
    op.drop_index(op.f('ix_expert_rules_project_id'), table_name='expert_rules', schema='api')
    op.drop_table('expert_rules', schema='api')
