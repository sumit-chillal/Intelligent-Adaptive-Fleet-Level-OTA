"""plain unique index on device_events.msg_id

HAND-WRITTEN. Alembic generated this file empty and it was replaced by hand.

WHY AUTOGENERATE MISSED IT
--------------------------
Alembic's autogenerate compares tables, columns, and which columns an index
covers. It does NOT compare index PREDICATES, so it saw

    UNIQUE btree (msg_id) WHERE msg_id IS NOT NULL     (in the database)
    UNIQUE btree (msg_id)                              (in the model)

as the same index and emitted `pass`.

WHY THE CHANGE IS NEEDED
------------------------
Postgres will only use a PARTIAL index to resolve an ON CONFLICT clause if the
statement repeats the identical predicate via index_where. Our idempotent event
insert uses a plain `ON CONFLICT (msg_id) DO NOTHING`, which could not match the
partial index, so every message carrying a msg_id failed with

    InvalidColumnReferenceError: there is no unique or exclusion constraint
    matching the ON CONFLICT specification

A plain unique index fixes it. Postgres treats NULLs as distinct in a unique
index, so the server-written rows that carry no msg_id still never collide with
one another, and idempotency is preserved for the rows that do.

Revision ID: 270b34c4aa5b
Revises: 3f94e40d4cd0
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "270b34c4aa5b"
down_revision: str | None = "3f94e40d4cd0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF EXISTS keeps this safe on a database where the index was never
    # created, e.g. a teammate setting up from scratch.
    op.execute("DROP INDEX IF EXISTS uq_events_msg_id")
    op.execute("CREATE UNIQUE INDEX uq_events_msg_id ON device_events (msg_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_events_msg_id")
    op.execute(
        "CREATE UNIQUE INDEX uq_events_msg_id ON device_events (msg_id) "
        "WHERE msg_id IS NOT NULL"
    )