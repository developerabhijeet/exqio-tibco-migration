"""seed pattern label: rsi-seed becomes seed in knowledge.memory_items

The knowledge schema is created by the engine (``engine/knowledge/schema.sql``), not by Alembic,
and may not exist yet on a fresh database; there is then nothing to relabel (new seeds are
written with the new label).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXISTS = "SELECT to_regclass('knowledge.memory_items') IS NOT NULL"


def upgrade() -> None:
    conn = op.get_bind()
    if not conn.execute(sa.text(EXISTS)).scalar():
        return
    conn.execute(sa.text(
        "UPDATE knowledge.memory_items SET source = 'seed', created_by = 'seed', "
        "context = jsonb_set(context, '{authored_by}', '\"ExiQO\"') WHERE source = 'rsi-seed'"))


def downgrade() -> None:
    conn = op.get_bind()
    if not conn.execute(sa.text(EXISTS)).scalar():
        return
    conn.execute(sa.text(
        "UPDATE knowledge.memory_items SET source = 'rsi-seed', created_by = 'rsi', "
        "context = jsonb_set(context, '{authored_by}', '\"R Systems\"') WHERE source = 'seed'"))
