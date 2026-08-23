"""
CONVOY — ingestion: validated messages become durable, auditable state.

Two write paths, deliberately different:

  EVENT LOG (device_events)   append-only, never updated. State CHANGES only.
  PROJECTIONS (devices)       current state, upserted. Rebuildable from events.

WHY HEALTH SAMPLES DO NOT GO INTO THE EVENT LOG
-----------------------------------------------
Fifteen devices reporting every five seconds is three writes per second. Ten
thousand devices is two thousand per second, and within a day the event log
would be 99.9% routine telemetry with the handful of rows that actually explain
a failure buried inside it. So health goes to its own time-series table
(device_health_samples) and the event log records only things that CHANGED:
came online, went offline, was offered an update, failed, rolled back.

That keeps the audit query in Requirement 14 both fast and readable.

IDEMPOTENCY
-----------
QoS 1 guarantees at-least-once delivery, so every handler must tolerate seeing
the same message twice -- on a reconnect, or when the broker retries an
unacknowledged publish. Every event insert uses ON CONFLICT DO NOTHING against
the partial unique index on msg_id. This is exactly-once EFFECT built on
at-least-once DELIVERY, which is the only combination that is actually
achievable over an unreliable network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import EventType
from app.core.firmware import safe_version_code
from app.db.models import Device, DeviceEvent, DeviceHealthSample
from app.schemas.mqtt import HealthMessage, HelloMessage, StatusMessage
from app.services.eventbus import Channel, bus

log = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ingestor:
    """All writes triggered by inbound device traffic."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- events --
    async def record_event(
        self,
        *,
        device_id: str,
        event_type: EventType,
        reason_code: str | None = None,
        campaign_id: str | None = None,
        batch_id: int | None = None,
        battery: int | None = None,
        network_quality: int | None = None,
        payload: dict | None = None,
        msg_id: str | None = None,
        source: str = "device",
    ) -> None:
        """Append one row. Silently ignores a duplicate msg_id."""
        stmt = pg_insert(DeviceEvent).values(
            device_id=device_id,
            campaign_id=campaign_id,
            batch_id=batch_id,
            event_type=str(event_type),
            reason_code=reason_code,
            battery_at_event=battery,
            network_at_event=network_quality,
            payload=payload,
            msg_id=msg_id,
            source=source,
            ts=_utcnow(),
        )
        if msg_id:
            stmt = stmt.on_conflict_do_nothing(index_elements=[DeviceEvent.msg_id])
        await self.session.execute(stmt)

        # Push to the live bus as well as the log. The database is the record;
        # the bus is the notification.
        bus.publish(Channel.EVENT, {
            "device_id": device_id, "campaign_id": campaign_id,
            "event_type": str(event_type), "reason_code": reason_code,
            "battery": battery, "network_quality": network_quality,
            "payload": payload,
        })

    # -------------------------------------------------------------- hello --
    async def handle_hello(self, device_id: str, msg: HelloMessage) -> bool:
        """Register or refresh a device. Returns True if newly registered."""
        existing = await self.session.scalar(
            select(Device).where(Device.device_id == device_id))
        is_new = existing is None

        values = {
            "device_id": device_id,
            "device_type": msg.device_type,
            "model": msg.model,
            "hw_rev": msg.hw_rev,
            "fleet_tag": msg.fleet_tag,
            "current_version": msg.current_version,
            # Derived here, once, at the only point a version enters the
            # system. Storing the string without the comparable form meant
            # every device read as version_code 0, and a rollback concluded
            # the entire fleet was below its target and skipped all of it.
            "current_version_code": safe_version_code(msg.current_version),
            "active_slot": msg.active_slot,
            "battery": msg.battery,
            "network_quality": msg.network_quality,
            "failure_profile": msg.failure_profile,
            "online": True,
            "last_seen_at": _utcnow(),
        }
        stmt = pg_insert(Device).values(**values)
        # A device that reconnects must update its row, not fail on the unique
        # constraint. registered_at is intentionally NOT in the update set --
        # first-seen time should never move.
        stmt = stmt.on_conflict_do_update(
            index_elements=[Device.device_id],
            set_={k: v for k, v in values.items() if k != "device_id"},
        )
        await self.session.execute(stmt)

        await self.record_event(
            device_id=device_id,
            event_type=EventType.DEVICE_REGISTERED if is_new else EventType.DEVICE_ONLINE,
            battery=msg.battery,
            network_quality=msg.network_quality,
            msg_id=msg.msg_id,
            payload={
                "version": msg.current_version,
                "model": msg.model,
                "fleet_tag": msg.fleet_tag,
                "agent": msg.agent,
                "trigger": msg.trigger,
                "resume_pending": msg.resume_pending,
            },
        )
        bus.publish(Channel.DEVICE, {
            "device_id": device_id, "online": True,
            "current_version": msg.current_version, "battery": msg.battery,
            "network_quality": msg.network_quality, "fleet_tag": msg.fleet_tag,
            "model": msg.model, "new": is_new,
        })
        return is_new

    # ------------------------------------------------------------- health --
    async def handle_health(self, device_id: str, msg: HealthMessage) -> None:
        """Time-series write plus a projection touch. No event row (see above).

        The device row is updated even if the device was never seen via hello,
        which can happen if the bridge starts after the fleet. The broadcast
        announce covers that case properly, but this makes the system
        self-healing either way.
        """
        self.session.add(DeviceHealthSample(
            device_id=device_id,
            battery=msg.battery,
            network_quality=msg.network_quality,
            uptime_s=msg.uptime_s,
            ts=_utcnow(),
        ))

        bus.publish(Channel.HEALTH, {
            "device_id": device_id, "battery": msg.battery,
            "network_quality": msg.network_quality,
            "current_version": msg.current_version, "online": True,
        })

        result = await self.session.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(
                battery=msg.battery,
                network_quality=msg.network_quality,
                online=True,
                last_seen_at=_utcnow(),
                **({"current_version": msg.current_version,
                    "current_version_code": safe_version_code(msg.current_version)}
                   if msg.current_version else {}),
            )
        )
        if result.rowcount == 0:
            # Health from a device we have no row for. Create a minimal one so
            # the sample's foreign key holds, and let the next hello fill in
            # the details.
            await self.session.execute(
                pg_insert(Device)
                .values(device_id=device_id, battery=msg.battery,
                        network_quality=msg.network_quality, online=True,
                        device_type=msg.device_type or "tcu-sim",
                        model=msg.model or "tcu-sim-v1",
                        current_version=msg.current_version,
                        current_version_code=safe_version_code(msg.current_version),
                        last_seen_at=_utcnow())
                .on_conflict_do_nothing(index_elements=[Device.device_id])
            )

    # ------------------------------------------------------------- status --
    async def handle_status(self, device_id: str, msg: StatusMessage) -> None:
        """Online/offline. Offline usually arrives via the broker's last will,
        which is why offline detection needs no polling at all."""
        await self.session.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(online=msg.online,
                    **({"last_seen_at": _utcnow()} if msg.online else {}))
        )
        bus.publish(Channel.DEVICE, {"device_id": device_id, "online": msg.online,
                                     "reason": msg.reason})
        await self.record_event(
            device_id=device_id,
            event_type=EventType.DEVICE_ONLINE if msg.online else EventType.DEVICE_OFFLINE,
            reason_code=msg.reason,
            msg_id=msg.msg_id,
            payload={"reason": msg.reason},
        )

    # ------------------------------------------------- offline reaper ------
    async def mark_stale_devices_offline(self, ttl_seconds: int) -> list[str]:
        """Catch devices that vanished without a last will firing.

        A last will covers a clean broker-detected disconnect. It does not
        cover a device whose network is up but whose process is wedged, so a
        heartbeat-timeout sweep is the backstop.
        """
        cutoff = _utcnow().timestamp() - ttl_seconds
        rows = await self.session.scalars(
            select(Device).where(Device.online.is_(True))
        )
        gone: list[str] = []
        for device in rows:
            if device.last_seen_at is None:
                continue
            if device.last_seen_at.timestamp() < cutoff:
                device.online = False
                gone.append(device.device_id)
                await self.record_event(
                    device_id=device.device_id,
                    event_type=EventType.DEVICE_OFFLINE,
                    reason_code="HEARTBEAT_TIMEOUT",
                    source="server",
                    payload={"ttl_seconds": ttl_seconds},
                )
        return gone