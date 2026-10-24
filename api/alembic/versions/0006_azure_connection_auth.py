"""Azure connection auth: the deploy environment and its credential reference (no secret value)

``auth_mode`` is ``local`` (Fernet-encrypted secret, local development), ``workload_identity`` or
``keyvault_sp``; the last two copy tenant, subscription and client id from a declared deploy
environment and keep only where the secret lives (``key_vault_uri``, ``secret_name``).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCES = ('client_id', 'key_vault_uri', 'secret_name')


def upgrade() -> None:
    op.add_column('azure_connections', sa.Column('environment', sa.Text(), nullable=True), schema='api')
    op.add_column('azure_connections', sa.Column('auth_mode', sa.Text(), nullable=False,
                                                 server_default='local'), schema='api')
    for name in REFERENCES:
        op.add_column('azure_connections', sa.Column(name, sa.Text(), nullable=True), schema='api')


def downgrade() -> None:
    for name in (*REFERENCES, 'auth_mode', 'environment'):
        op.drop_column('azure_connections', name, schema='api')
