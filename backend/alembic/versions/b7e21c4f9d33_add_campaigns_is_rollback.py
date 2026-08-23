"""add campaigns.is_rollback

A rollback is modelled as a campaign carrying a flag, not as a separate
mechanism. That keeps batching, eligibility, retries, the audit trail and the
adaptive engine identical between "roll forward" and "roll back" — a recovery
path that shares no code with the normal path is a recovery path nobody has
tested, and the one time it runs is the worst possible time to discover that.

The flag has two effects:
  * the signed manifest carries rollback:true, so a device accepts a LOWER
    version_code past its anti-rollback floor (only the real server can set
    this, because it is inside the signature);
  * the eligibility gate inverts its "already done" test, or the rollback
    would skip the entire fleet as already-up-to-date.

Revision ID: b7e21c4f9d33
Revises: a1d4c9e70b21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e21c4f9d33"
down_revision: str | None = "a1d4c9e70b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("is_rollback", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "is_rollback")