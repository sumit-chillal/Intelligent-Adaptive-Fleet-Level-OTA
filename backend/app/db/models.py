"""
CONVOY — database schema.

THE CENTRAL DESIGN DECISION: an append-only event log with projections.

Requirement 14 asks that it be possible to answer "what happened to device X
during campaign Y and why" from stored data alone. A schema that only keeps
current state cannot answer that: once tcu_D_005 moves from DOWNLOADING to
FAILED, the battery reading at the moment of failure, the chunk it reached, and
the reason are gone.

So `device_events` is the source of truth and is NEVER updated or deleted
(Rules.md R5). Corrections are new events. `devices` and `campaign_targets` are
PROJECTIONS -- fast, indexed, current-state views that could be dropped and
rebuilt entirely by replaying the event log. That is the same event-sourcing
shape used by real fleet telematics platforms, and it is what makes the audit
claim literally true rather than aspirational.

Practical consequences you will feel:
  * Every event row carries the battery and network reading captured AT THAT
    MOMENT, not a foreign key to a value that has since changed.
  * Ordering is by the server-assigned BIGSERIAL `id`, never by a device
    timestamp. Four laptops in different cities have four different clocks.
  * Timestamps are TIMESTAMPTZ in UTC. Display conversion is the UI's job.

WHY event_type AND reason_code ARE VARCHAR, NOT POSTGRES ENUMS
--------------------------------------------------------------
A native Postgres enum would give database-level validation, but adding one
value later requires an ALTER TYPE migration, and historical rows in an
append-only table must never be rewritten. The Python StrEnum in
app/constants.py is the source of truth; the database stores the string. This
trades a little database-side strictness for the ability to extend the
vocabulary without touching history.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(),
                         nullable=False)


# ============================================================== DEVICES ======
class Device(Base):
    """Projection of current device state. Rebuildable from device_events."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(String(32), default="tcu-sim")
    model: Mapped[str] = mapped_column(String(64), default="tcu-sim-v1")
    hw_rev: Mapped[str | None] = mapped_column(String(32))
    fleet_tag: Mapped[str | None] = mapped_column(String(64), index=True)

    current_version: Mapped[str | None] = mapped_column(String(32))
    current_version_code: Mapped[int] = mapped_column(Integer, default=0)
    min_allowed_version_code: Mapped[int] = mapped_column(Integer, default=0)
    active_slot: Mapped[str | None] = mapped_column(String(8))

    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    battery: Mapped[int | None] = mapped_column(Integer)
    network_quality: Mapped[int | None] = mapped_column(Integer)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Declared health profile from the device's own hello. Display only -- the
    # server never trusts this for eligibility, it uses live health samples.
    failure_profile: Mapped[dict | None] = mapped_column(JSONB)
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    registered_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("battery IS NULL OR (battery >= 0 AND battery <= 100)",
                        name="ck_devices_battery_range"),
        CheckConstraint("network_quality IS NULL OR "
                        "(network_quality >= 1 AND network_quality <= 5)",
                        name="ck_devices_network_range"),
        Index("ix_devices_online_seen", "online", "last_seen_at"),
    )


class DeviceHealthSample(Base):
    """Time series. Kept raw while a device is in an active campaign, then
    downsampled -- at 10,000 devices reporting every 5 s this is the highest
    volume table in the system by an order of magnitude."""

    __tablename__ = "device_health_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    battery: Mapped[int] = mapped_column(Integer, nullable=False)
    network_quality: Mapped[int] = mapped_column(Integer, nullable=False)
    uptime_s: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ix_health_device_ts", "device_id", "ts"),
    )


# ============================================================= FIRMWARE ======
class Firmware(Base):
    """Immutable once published. A new build is a new version, never an edit."""

    __tablename__ = "firmware"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firmware_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    version_code: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_hashes: Mapped[list] = mapped_column(JSONB, nullable=False)

    # The signature stored here is over the campaign-independent template.
    # The per-device manifest is signed at offer time, because it binds
    # device_id, campaign_id and a fresh nonce (see core/crypto.py).
    manifest_template: Mapped[dict | None] = mapped_column(JSONB)
    signature: Mapped[bytes | None] = mapped_column(LargeBinary)

    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("model", "version", name="uq_firmware_model_version"),
        Index("ix_firmware_state_model", "state", "model"),
    )


# ============================================================ CAMPAIGNS ======
class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    firmware_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("firmware.firmware_id", ondelete="RESTRICT"),
        nullable=False)

    selector: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)

    # A rollback is a campaign like any other -- same batching, same
    # eligibility gate, same audit trail, same adaptive engine. The only
    # differences are that the manifest carries a signed rollback flag (so
    # devices accept a LOWER version_code past their anti-rollback floor) and
    # that "already done" inverts direction.
    #
    # Modelling it as a separate mechanism would have meant duplicating the
    # orchestrator, and a recovery path that shares no code with the normal
    # path is a recovery path nobody has tested.
    is_rollback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ---- rollout policy: every knob the adaptive engine reads --------------
    batch_size_initial: Mapped[int] = mapped_column(Integer, default=5)
    batch_size_min: Mapped[int] = mapped_column(Integer, default=1)
    batch_size_max: Mapped[int] = mapped_column(Integer, default=20)
    canary_size: Mapped[int] = mapped_column(Integer, default=2)
    shrink_threshold: Mapped[float] = mapped_column(Float, default=0.20)
    abort_threshold: Mapped[float] = mapped_column(Float, default=0.40)
    grow_after_clean_batches: Mapped[int] = mapped_column(Integer, default=2)
    ewma_alpha: Mapped[float] = mapped_column(Float, default=0.5)

    # ---- eligibility gate --------------------------------------------------
    min_battery: Mapped[int] = mapped_column(Integer, default=30)
    min_network_quality: Mapped[int] = mapped_column(Integer, default=2)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    batch_timeout_seconds: Mapped[int] = mapped_column(Integer, default=180)

    # ---- live rollout state, carried between adaptive decisions ------------
    current_batch_size: Mapped[int] = mapped_column(Integer, default=5)
    ewma_failure_rate: Mapped[float] = mapped_column(Float, default=0.0)
    clean_streak: Mapped[int] = mapped_column(Integer, default=0)
    batches_completed: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    targets: Mapped[list["CampaignTarget"]] = relationship(back_populates="campaign")

    __table_args__ = (
        CheckConstraint("batch_size_min >= 1", name="ck_campaign_min_batch"),
        CheckConstraint("batch_size_max >= batch_size_min", name="ck_campaign_max_batch"),
        CheckConstraint("shrink_threshold > 0 AND shrink_threshold <= abort_threshold "
                        "AND abort_threshold <= 1", name="ck_campaign_thresholds"),
        Index("ix_campaigns_state", "state"),
    )


class CampaignTarget(Base):
    """One row per device per campaign. The unit the orchestrator schedules."""

    __tablename__ = "campaign_targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False)

    state: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reason_code: Mapped[str | None] = mapped_column(String(48))
    from_version: Mapped[str | None] = mapped_column(String(32))
    to_version: Mapped[str | None] = mapped_column(String(32))

    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("batches.id", ondelete="SET NULL"))

    # Resume state, mirrored from the device so the dashboard can show a bar
    # that restarts at 55% rather than 0% (Requirement 11).
    last_chunk_index: Mapped[int] = mapped_column(Integer, default=-1)
    offer_nonce: Mapped[str | None] = mapped_column(String(64))

    # How many times this device was passed over for a TRANSIENT reason
    # (offline, low battery, weak signal) rather than attempted.
    #
    # Separate from `attempts` on purpose: an attempt is a real update that
    # happened and can inform the failure rate, while a deferral is the system
    # declining to try. Conflating them would let a parked car's overnight
    # unavailability exhaust its retry budget.
    deferrals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped["Campaign"] = relationship(back_populates="targets")

    __table_args__ = (
        UniqueConstraint("campaign_id", "device_id", name="uq_target_campaign_device"),
        # The orchestrator's hottest query: "give me PENDING targets for this
        # campaign". Without this index it is a sequential scan every tick.
        Index("ix_targets_campaign_state", "campaign_id", "state"),
    )


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_size: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_size: Mapped[int] = mapped_column(Integer, default=0)
    is_canary: Mapped[bool] = mapped_column(Boolean, default=False)

    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)

    opened_at: Mapped[datetime] = _now()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("campaign_id", "index", name="uq_batch_campaign_index"),
    )


class RolloutDecision(Base):
    """Every adaptive decision, with the numbers that produced it.

    This table IS the analytics batch-size chart and the terminal banner. It
    exists so that "why did the batch shrink" is answerable months later
    without re-deriving anything.
    """

    __tablename__ = "rollout_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False)
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("batches.id", ondelete="SET NULL"))
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)

    prev_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    new_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    ewma_failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    attempted: Mapped[int] = mapped_column(Integer, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False)

    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ix_decisions_campaign_ts", "campaign_id", "ts"),
    )


# =============================================== THE APPEND-ONLY EVENT LOG ===
class DeviceEvent(Base):
    """The source of truth. NEVER UPDATE. NEVER DELETE. (Rules.md R5)

    Answering Requirement 14 is one query:

        SELECT ts, event_type, reason_code, battery_at_event,
               network_at_event, payload
        FROM device_events
        WHERE device_id = :device AND campaign_id = :campaign
        ORDER BY id;

    Note it orders by `id`, not `ts`. `id` is assigned by Postgres in commit
    order, so it is a true sequence. `ts` comes from whichever machine wrote
    the row, and machines in different cities disagree about the time.
    """

    __tablename__ = "device_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[int | None] = mapped_column(BigInteger)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(48))

    # The health readings AT THE MOMENT of the event. Denormalised on purpose:
    # a foreign key would point at a value that has since changed, which would
    # make the audit trail lie about why a decision was made.
    battery_at_event: Mapped[int | None] = mapped_column(Integer)
    network_at_event: Mapped[int | None] = mapped_column(Integer)

    payload: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(16), default="device")
    msg_id: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = _now()

    __table_args__ = (
        # The audit query above.
        Index("ix_events_device_campaign", "device_id", "campaign_id", "id"),
        # The dashboard's "recent activity" feed.
        Index("ix_events_ts", "ts"),
        # Idempotency: the same MQTT message may be delivered twice at QoS 1.
        #
        # This was originally a PARTIAL unique index (WHERE msg_id IS NOT NULL)
        # to avoid indexing the many rows the server writes without a msg_id.
        # That does not work with ON CONFLICT: Postgres will only use a partial
        # index for conflict resolution if the statement repeats the exact same
        # predicate via index_where, and any mismatch raises
        #   "there is no unique or exclusion constraint matching the
        #    ON CONFLICT specification"
        # A plain unique index is used instead. Postgres treats NULLs as
        # distinct in a unique index, so server-generated rows with no msg_id
        # never collide with each other, and the ON CONFLICT clause stays
        # simple. The cost is indexing those NULL rows, which is negligible.
        Index("uq_events_msg_id", "msg_id", unique=True),
    )


class AuditLog(Base):
    """Operator actions. Separate from device_events because the actor is a
    human and the questions asked of it are different ('who aborted the
    campaign?' rather than 'what happened to this device?')."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ts: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id", "ts"),
    )