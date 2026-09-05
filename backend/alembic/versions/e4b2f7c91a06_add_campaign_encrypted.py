"""add campaigns.encrypted

Moves firmware encryption from a global setting to a per-campaign policy.

A global flag assumes every device in the fleet has the same capabilities. That
held while the fleet was fifteen identical containers, and stopped holding the
moment a hardware device joined: the ESP32 publishes no X25519 public key until
its own encryption support is written, so a global flag either excluded it from
every campaign or forced the entire fleet back to plaintext.

As a campaign column it becomes what it always should have been — a policy
chosen per rollout, alongside batch size and the health thresholds — and each
campaign records what it actually did rather than what the server happened to
be configured for at the time.

Existing rows default to false. That is deliberate: campaigns already in the
database ran before this column existed, and the honest value for "was this
rollout encrypted" is the one that reflects how it actually ran.

Revision ID: e4b2f7c91a06
Revises: d5a91c72e88f
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b2f7c91a06"
down_revision: str | None = "d5a91c72e88f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("encrypted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "encrypted")
