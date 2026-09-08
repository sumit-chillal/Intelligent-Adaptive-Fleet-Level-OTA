"""add campaigns.device_min_battery

Separates the battery threshold the SERVER filters on from the one the DEVICE
checks against.

The server's copy of a device's battery is at least a heartbeat old, so it
filters cheaply to avoid transferring a megabyte to a device that plainly
cannot take it. The device then checks again against its own live reading,
which is the one that actually matters -- its condition can change between the
decision to update it and the arrival of the offer.

With a single threshold the two checks can never be observed separately: a
device below it is skipped server-side and never learns it was considered. With
the server threshold low and this one high, the offer is sent and the device
refuses on its own reading, which is the more realistic behaviour and the only
version of it that is visible on the hardware.

NULL means "same as min_battery", so existing campaigns are unchanged.

Revision ID: f6c3e8b21d94
Revises: e4b2f7c91a06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c3e8b21d94"
down_revision: str | None = "e4b2f7c91a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns",
                  sa.Column("device_min_battery", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "device_min_battery")
