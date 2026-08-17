"""
CONVOY — campaign creation and target selection.

A campaign is a plan: this firmware, to these devices, under this risk policy.
Creating one materialises a `campaign_targets` row per matched device, all
PENDING. Those rows are the unit the orchestrator schedules, and they are also
the permanent record of who was supposed to receive what.

WHY TARGETS ARE MATERIALISED UP FRONT
-------------------------------------
The alternative -- re-running the selector query on every batch -- would mean
the campaign's membership silently changes as devices connect and disconnect.
A device that joined halfway through would appear in the results of a campaign
it was never part of, and "how many devices did this rollout cover?" would have
no stable answer. Freezing the target list at creation makes the campaign a
closed set, which is what makes the analytics meaningful.

Eligibility is evaluated LATER, per batch, against live health. Membership is
fixed; readiness is not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import CampaignState, EventType, TargetState
from app.core.eligibility import DeviceSnapshot, EligibilityPolicy, evaluate
from app.db.models import Campaign, CampaignTarget, Device, Firmware
from app.services.ingest import Ingestor

log = structlog.get_logger(__name__)


class CampaignError(Exception):
    pass


@dataclass(frozen=True)
class Selector:
    """Which devices this campaign covers.

    Empty means "every device of the firmware's model", which is the common
    case and keeps the demo command short.
    """

    models: list[str] | None = None
    fleet_tags: list[str] | None = None
    device_ids: list[str] | None = None

    def to_dict(self) -> dict:
        return {"models": self.models, "fleet_tags": self.fleet_tags,
                "device_ids": self.device_ids}

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Selector":
        raw = raw or {}
        return cls(models=raw.get("models"), fleet_tags=raw.get("fleet_tags"),
                   device_ids=raw.get("device_ids"))


async def select_devices(session: AsyncSession, selector: Selector,
                         default_model: str) -> list[Device]:
    if selector.device_ids:
        rows = list(await session.scalars(
            select(Device).where(Device.device_id.in_(selector.device_ids))))
        # Preserve the ORDER the operator gave, not the database's.
        #
        # Batch membership follows target order, so ordering is a real rollout
        # control: it decides which devices are in the canary and where any
        # known-risky device lands. Alphabetical order would put tcu_D_004 and
        # tcu_D_005 in the final batch every time, so the campaign would shrink
        # on its last batch with nothing left to demonstrate the smaller size.
        # An operator staging a rollout should be able to say "these two first,
        # these last", and the obvious way to express that is the order they
        # typed.
        position = {device_id: i for i, device_id in enumerate(selector.device_ids)}
        return sorted(rows, key=lambda d: position.get(d.device_id, len(position)))

    stmt = select(Device).where(Device.model.in_(selector.models or [default_model]))
    if selector.fleet_tags:
        stmt = stmt.where(Device.fleet_tag.in_(selector.fleet_tags))
    return list(await session.scalars(stmt.order_by(Device.device_id)))


@dataclass
class DryRunEntry:
    device_id: str
    eligible: bool
    reason: str | None
    detail: str
    battery: int | None
    network_quality: int | None


async def dry_run(session: AsyncSession, *, firmware_id: str,
                  selector: Selector, min_battery: int,
                  min_network_quality: int) -> list[DryRunEntry]:
    """What WOULD happen, without committing anything.

    This exists because an operator should never have to start a rollout to
    find out which devices it will skip. Showing the skip list before the
    commit point is the difference between a tool an engineer trusts and one
    they poke at nervously.
    """
    firmware = await session.scalar(
        select(Firmware).where(Firmware.firmware_id == firmware_id))
    if firmware is None:
        raise CampaignError(f"unknown firmware_id {firmware_id!r}")

    devices = await select_devices(session, selector, firmware.model)
    policy = EligibilityPolicy(
        min_battery=min_battery,
        min_network_quality=min_network_quality,
        target_version_code=firmware.version_code,
        offline_ttl_seconds=settings.device_offline_ttl_seconds,
    )

    out: list[DryRunEntry] = []
    for device in devices:
        result = evaluate(_snapshot(device), policy)
        out.append(DryRunEntry(
            device_id=device.device_id,
            eligible=result.eligible,
            reason=str(result.reason) if result.reason else None,
            detail=result.detail,
            battery=device.battery,
            network_quality=device.network_quality,
        ))
    return out


def _snapshot(device: Device) -> DeviceSnapshot:
    return DeviceSnapshot(
        device_id=device.device_id,
        online=device.online,
        battery=device.battery,
        network_quality=device.network_quality,
        current_version_code=device.current_version_code or 0,
        model=device.model,
    )


async def create_campaign(
    session: AsyncSession,
    *,
    name: str,
    firmware_id: str,
    selector: Selector | None = None,
    batch_size: int | None = None,
    batch_size_min: int | None = None,
    batch_size_max: int | None = None,
    canary_size: int | None = None,
    min_battery: int | None = None,
    min_network_quality: int | None = None,
    shrink_threshold: float | None = None,
    abort_threshold: float | None = None,
    max_attempts: int | None = None,
    created_by: str = "cli",
) -> tuple[Campaign, int]:
    """Create a campaign and materialise its targets. Returns (campaign, count)."""

    firmware = await session.scalar(
        select(Firmware).where(Firmware.firmware_id == firmware_id))
    if firmware is None:
        raise CampaignError(f"unknown firmware_id {firmware_id!r}")
    if firmware.state != "PUBLISHED":
        raise CampaignError(
            f"firmware {firmware.version} is {firmware.state}, not PUBLISHED. "
            f"Only published firmware can be campaigned.")

    selector = selector or Selector()
    devices = await select_devices(session, selector, firmware.model)
    if not devices:
        raise CampaignError(
            f"selector matched no devices. Are any devices connected, and does "
            f"their model match {firmware.model!r}?")

    campaign_id = f"c_{uuid.uuid4().hex[:10]}"
    campaign = Campaign(
        campaign_id=campaign_id,
        name=name,
        firmware_id=firmware_id,
        selector=selector.to_dict(),
        state=str(CampaignState.DRAFT),
        batch_size_initial=batch_size or settings.default_batch_size,
        batch_size_min=batch_size_min or settings.default_batch_size_min,
        batch_size_max=batch_size_max or settings.default_batch_size_max,
        canary_size=canary_size if canary_size is not None else settings.default_canary_size,
        shrink_threshold=shrink_threshold if shrink_threshold is not None
        else settings.shrink_threshold,
        # Default comes from settings, but see Memory.md D15: the demo runs at
        # 0.50 because the scripted 2-of-5 failure is exactly 40%, which would
        # abort rather than shrink.
        abort_threshold=abort_threshold if abort_threshold is not None
        else settings.abort_threshold,
        grow_after_clean_batches=settings.grow_after_clean_batches,
        ewma_alpha=settings.ewma_alpha,
        min_battery=min_battery if min_battery is not None else settings.default_min_battery,
        min_network_quality=min_network_quality if min_network_quality is not None
        else settings.default_min_network_quality,
        # 1 means "no retries": a failed device stays failed.
        #
        # Retries are the right default for a real fleet -- most failures are
        # transient. But a device that is PERMANENTLY unfit (8% battery that
        # never charges) fails every attempt, and because retried targets are
        # scheduled after the untried ones, the final batch can end up
        # containing nothing but repeat offenders. That batch is 100% failures
        # and trips the abort guard, halting a campaign whose healthy devices
        # were all updating fine. Set max_attempts=1 when you want failures to
        # be final and the rollout to carry on.
        max_attempts=max_attempts if max_attempts is not None
        else settings.max_attempts_per_device,
        batch_timeout_seconds=settings.batch_timeout_seconds,
        current_batch_size=batch_size or settings.default_batch_size,
        created_by=created_by,
    )
    session.add(campaign)
    await session.flush()

    ingestor = Ingestor(session)
    for device in devices:
        session.add(CampaignTarget(
            campaign_id=campaign_id,
            device_id=device.device_id,
            state=str(TargetState.PENDING),
            from_version=device.current_version,
            to_version=firmware.version,
        ))
        await ingestor.record_event(
            device_id=device.device_id,
            campaign_id=campaign_id,
            event_type=EventType.CAMPAIGN_CREATED,
            battery=device.battery,
            network_quality=device.network_quality,
            source="server",
            payload={"firmware_id": firmware_id, "to_version": firmware.version,
                     "from_version": device.current_version},
        )

    await session.flush()
    log.info("campaign_created", campaign_id=campaign_id, name=name,
             firmware=firmware.version, targets=len(devices),
             batch_size=campaign.batch_size_initial,
             abort_threshold=campaign.abort_threshold)
    return campaign, len(devices)


async def campaign_progress(session: AsyncSession, campaign_id: str) -> dict:
    """Counts by target state, for the CLI and later the dashboard."""
    rows = await session.execute(
        select(CampaignTarget.state, func.count())
        .where(CampaignTarget.campaign_id == campaign_id)
        .group_by(CampaignTarget.state)
    )
    counts = {state: n for state, n in rows.all()}
    return {
        "total": sum(counts.values()),
        "by_state": counts,
        "succeeded": counts.get(str(TargetState.SUCCEEDED), 0),
        "failed": counts.get(str(TargetState.FAILED), 0),
        "skipped": counts.get(str(TargetState.SKIPPED), 0),
        "pending": counts.get(str(TargetState.PENDING), 0),
    }