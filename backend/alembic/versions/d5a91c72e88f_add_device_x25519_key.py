"""add devices.x25519_public_key

Firmware confidentiality needs somewhere to record each device's X25519 public
key. The device generates the pair on first boot, keeps the private half in its
state file, and publishes the public half in every hello; the server wraps a
per-campaign AES content key to it so that only that device can unwrap it.

Nullable on purpose. Devices provisioned before this feature have no key, and
the orchestrator skips them with a clear reason rather than silently shipping
their firmware in plaintext -- a fleet believed to be encrypted but partly not
is worse than a visible refusal.

Revision ID: d5a91c72e88f
Revises: c3f8a1e64b57
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a91c72e88f"
down_revision: str | None = "c3f8a1e64b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices",
                  sa.Column("x25519_public_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "x25519_public_key")
