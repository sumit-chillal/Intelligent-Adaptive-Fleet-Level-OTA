"""backfill devices.current_version_code from current_version

The column existed from the initial schema but nothing ever wrote to it, so
every device sat at 0. Nothing depended on it until rollback arrived — and a
rollback compares version codes to decide who is above the target. With every
device reading 0, the first real rollback concluded the entire fleet was BELOW
its target and skipped all thirteen devices.

The bug was invisible for weeks because forward campaigns compare
`current_version_code >= target`, and 0 is below every target, so every device
looked eligible — which happened to be the right answer for the wrong reason.

This backfills existing rows. New writes are handled in app/services/ingest.py.

Revision ID: c3f8a1e64b57
Revises: b7e21c4f9d33
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3f8a1e64b57"
down_revision: str | None = "b7e21c4f9d33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # MAJOR*10000 + MINOR*100 + PATCH, matching core/firmware.version_to_code.
    # Only rows whose version looks like plain semver are touched; anything
    # else keeps 0, which is the same "older than everything" default the
    # Python parser falls back to.
    op.execute(
        """
        UPDATE devices
        SET current_version_code =
            split_part(current_version, '.', 1)::int * 10000
          + split_part(current_version, '.', 2)::int * 100
          + split_part(current_version, '.', 3)::int
        WHERE current_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'
        """
    )


def downgrade() -> None:
    op.execute("UPDATE devices SET current_version_code = 0")
