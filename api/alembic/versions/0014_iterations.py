"""iteration loop: ``iterations``, ``iteration_inputs``, ``edit_sets``, ``project_repos``

Design records and builds gain ``iteration`` (default 1: every existing row belongs to the implicit
iteration 1, created on first read), builds gain ``edit_set_id`` (a foreign key added after
``edit_sets``, which refers to builds) and rules ``source``.

Revision ID: 0014_iterations
Revises: 0013
Create Date: 2026-10-26 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0014_iterations'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text('now()')


def upgrade() -> None:
    op.create_table(
        'iterations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.Column('process_ids', JSONB, nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('resume_phase', sa.String(), nullable=False),
        sa.Column('requirements', sa.Text(), nullable=True),
        sa.Column('baseline_build_id', sa.String(), nullable=True),
        sa.Column('source', JSONB, nullable=True),
        sa.Column('opened_by', sa.String(), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['baseline_build_id'], ['api.builds.id'],
                                name=op.f('fk_iterations_baseline_build_id_builds'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'],
                                name=op.f('fk_iterations_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_iterations')),
        sa.UniqueConstraint('project_id', 'n', name=op.f('uq_iterations_project_id_n')),
        schema='api',
    )
    op.create_index(op.f('ix_iterations_project_id'), 'iterations', ['project_id'], unique=False, schema='api')

    op.create_table(
        'iteration_inputs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('iteration_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('process_id', sa.String(), nullable=True),
        sa.Column('ref', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['iteration_id'], ['api.iterations.id'],
                                name=op.f('fk_iteration_inputs_iteration_id_iterations'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_iteration_inputs')),
        schema='api',
    )
    op.create_index(op.f('ix_iteration_inputs_iteration_id'), 'iteration_inputs', ['iteration_id'],
                    unique=False, schema='api')

    op.create_table(
        'edit_sets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('iteration_id', sa.String(), nullable=False),
        sa.Column('baseline_build_id', sa.String(), nullable=True),
        sa.Column('files', JSONB, nullable=False),
        sa.Column('root_dir', sa.String(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['baseline_build_id'], ['api.builds.id'],
                                name=op.f('fk_edit_sets_baseline_build_id_builds'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['iteration_id'], ['api.iterations.id'],
                                name=op.f('fk_edit_sets_iteration_id_iterations'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_edit_sets')),
        schema='api',
    )
    op.create_index(op.f('ix_edit_sets_iteration_id'), 'edit_sets', ['iteration_id'], unique=False, schema='api')

    op.create_table(
        'project_repos',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('owner', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('default_branch', sa.String(), nullable=False),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('process_id', sa.String(), nullable=True),
        sa.Column('last_commit', sa.String(), nullable=True),
        sa.Column('connected_by', sa.String(), nullable=False),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'],
                                name=op.f('fk_project_repos_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_project_repos')),
        schema='api',
    )
    op.create_index(op.f('ix_project_repos_project_id'), 'project_repos', ['project_id'], unique=False,
                    schema='api')

    op.add_column('design_records', sa.Column('iteration', sa.Integer(), server_default='1', nullable=False),
                  schema='api')
    op.add_column('builds', sa.Column('iteration', sa.Integer(), server_default='1', nullable=False), schema='api')
    op.add_column('builds', sa.Column('edit_set_id', sa.String(), nullable=True), schema='api')
    op.create_index(op.f('ix_builds_edit_set_id'), 'builds', ['edit_set_id'], unique=False, schema='api')
    op.create_foreign_key(op.f('fk_builds_edit_set_id_edit_sets'), 'builds', 'edit_sets', ['edit_set_id'], ['id'],
                          source_schema='api', referent_schema='api', ondelete='SET NULL')
    op.add_column('rules', sa.Column('source', sa.String(), nullable=True), schema='api')


def downgrade() -> None:
    op.drop_column('rules', 'source', schema='api')
    op.drop_constraint(op.f('fk_builds_edit_set_id_edit_sets'), 'builds', type_='foreignkey', schema='api')
    op.drop_index(op.f('ix_builds_edit_set_id'), table_name='builds', schema='api')
    op.drop_column('builds', 'edit_set_id', schema='api')
    op.drop_column('builds', 'iteration', schema='api')
    op.drop_column('design_records', 'iteration', schema='api')
    op.drop_index(op.f('ix_project_repos_project_id'), table_name='project_repos', schema='api')
    op.drop_table('project_repos', schema='api')
    op.drop_index(op.f('ix_edit_sets_iteration_id'), table_name='edit_sets', schema='api')
    op.drop_table('edit_sets', schema='api')
    op.drop_index(op.f('ix_iteration_inputs_iteration_id'), table_name='iteration_inputs', schema='api')
    op.drop_table('iteration_inputs', schema='api')
    op.drop_index(op.f('ix_iterations_project_id'), table_name='iterations', schema='api')
    op.drop_table('iterations', schema='api')
