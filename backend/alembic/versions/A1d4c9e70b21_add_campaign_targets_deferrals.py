"""add campaign_targets.deferrals

Distinguishes "we declined to try this device" from "we tried and it failed".

A device skipped because it was offline or low on battery has not failed -- the
system chose not to attempt it. Before this column, such a skip was terminal,
so a broker reconnect mid-campaign permanently wrote off two healthy devices
that would have updated fine on the next pass.

`attempts` counts real update attempts and feeds the retry budget.
`deferrals` counts passes-over and is bounded separately, so a parked car's
overnight unavailability cannot exhaust its retry budget.

Revision ID: a1d4c9e70b21
Revises: 270b34c4aa5b
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4c9e70b21"
down_revision: str | None = "270b34c4aa5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default is required: existing rows need a value, and NOT NULL
    # without one would fail on a table that already has campaign history.
    op.add_column(
        "campaign_targets",
        sa.Column("deferrals", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("campaign_targets", "deferrals")