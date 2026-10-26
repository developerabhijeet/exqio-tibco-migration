"""ExiQoian chat: ``chat_conversations`` and ``chat_messages``

Conversations are private to their owner; messages hold the masked text, the cited text (JSONB)
and the verdict. Both cascade from the project.

Revision ID: 0013
Revises: 0010 (temporary: becomes '0012_iterations' when SP6 lands (X25))
Create Date: 2026-10-26 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0013'
# Temporary parent for the integration rehearsal: becomes '0012_iterations' when SP6 lands (X25).
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_conversations',
        sa.Column('project_id', sa.Text(), nullable=False),
        sa.Column('owner', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), server_default='', nullable=False),
        sa.Column('persona', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['api.projects.id'],
                                name=op.f('fk_chat_conversations_project_id_projects'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_conversations')),
        schema='api',
    )
    op.create_index('ix_chat_conversations_project_id_owner_updated_at', 'chat_conversations',
                    ['project_id', 'owner', 'updated_at'], unique=False, schema='api')
    op.create_table(
        'chat_messages',
        sa.Column('conversation_id', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('cited', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('verdict', sa.Text(), nullable=True),
        sa.Column('persona', sa.Text(), nullable=True),
        sa.Column('used_model', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('provider', sa.Text(), nullable=True),
        sa.Column('rule', sa.Text(), nullable=True),
        sa.Column('dropped_sources', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['api.chat_conversations.id'],
                                name=op.f('fk_chat_messages_conversation_id_chat_conversations'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages')),
        schema='api',
    )
    op.create_index('ix_chat_messages_conversation_id_created_at', 'chat_messages',
                    ['conversation_id', 'created_at'], unique=False, schema='api')


def downgrade() -> None:
    op.drop_index('ix_chat_messages_conversation_id_created_at', table_name='chat_messages', schema='api')
    op.drop_table('chat_messages', schema='api')
    op.drop_index('ix_chat_conversations_project_id_owner_updated_at', table_name='chat_conversations', schema='api')
    op.drop_table('chat_conversations', schema='api')
