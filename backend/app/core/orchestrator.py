"""
CONVOY — the campaign orchestrator.

The campaign state machine. It owns the question "who gets firmware next, and
how many at a time", and it is the component that actually calls the adaptive
engine.

WHAT IT DOES NOT DO
-------------------
It never touches MQTT directly -- it asks the bridge to publish. It never makes
the resize decision itself -- it hands a batch outcome to a pure function and
records what comes back. Keeping those two boundaries clean is what makes the
rollout logic testable and the decision auditable.

THE LOOP
--------
    tick()
      for each RUNNING campaign:
        if a batch is open      -> check whether it has finished or timed out
        if no batch is open     -> open the next one

    open_batch()
      plan size (canary on batch 1, else the engine's current size)
      take that many PENDING targets
      evaluate ELIGIBILITY against live health
        ineligible -> SKIPPED_*, recorded, never offered, excluded from the rate
        eligible   -> signed offer published

    close_batch()
      collect outcomes -> decide() -> persist decision -> print banner
      ABORT stops the campaign; otherwise the new batch size takes effect

WHY A SINGLE LEADER
-------------------
Two orchestrators running the same campaign would both open batch N and
double-offer to the same devices. In a single-process demo that cannot happen,
but the Redis lease in Phase 3 is what makes the design honest at scale, so the
code is written as if it were already there: all state lives in Postgres, and
nothing is cached across ticks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import (
    CampaignState,
    DecisionAction,
    EventType,
    ReasonCode,
    TargetState,
    counts_as_failure,
)
from app.core.adaptive import BatchOutcome, Decision, RolloutPolicy, RolloutState, decide
from app.core.crypto import sign_manifest
from app.core.eligibility import DeviceSnapshot, EligibilityPolicy, evaluate
from app.core.firmware import FirmwarePackage, build_manifest
from app.db.models import Batch, Campaign, CampaignTarget, Device, Firmware, RolloutDecision
from app.db.session import session_scope
from app.mqtt import topics
from app.services.firmware_service import load_package, load_signing_key
from app.services.ingest import Ingestor

log = structlog.get_logger(__name__)

# How many times a device may be passed over for a transient reason before the
# campaign gives up on it. Three passes is enough to survive a broker reconnect
# or a device rebooting, without keeping a rollout open for a machine that is
# switched off.
MAX_DEFERRALS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Orchestrator:
    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._packages: dict[str, FirmwarePackage] = {}
        self._key = None
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._warmed = False

    # ------------------------------------------------------------ lifecycle
    async def run(self, interval_s: float = 2.0) -> None:
        """Tick forever, but never on a stale picture of the fleet.

        The orchestrator's first act on a batch is to judge every target's
        health, and that judgement is only as good as the last heartbeat in the
        database. On a cold start those rows can be hours old -- the bridge has
        not connected yet, no device has said anything, and every target looks
        offline. Opening a batch in that window marks the whole fleet
        SKIPPED_OFFLINE and completes the campaign having done nothing.

        So: wait for the broker connection, then wait one warm-up window on top
        so every online device has heartbeated at least once. The same applies
        after a reconnect, because status changes during the outage were missed.
        """
        while not self._stop.is_set():
            if not self.bridge.ready.is_set():
                self._warmed = False
                log.info("orchestrator_waiting_for_broker")
                await self.bridge.ready.wait()

            if not self._warmed:
                log.info("orchestrator_warming_up",
                         seconds=settings.orchestrator_warmup_seconds,
                         why="letting devices report before judging their health")
                await asyncio.sleep(settings.orchestrator_warmup_seconds)
                self._warmed = True
                log.info("orchestrator_ready")

            try:
                await self.tick()
            except Exception:
                log.exception("orchestrator_tick_failed")
            await asyncio.sleep(interval_s)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._stream_tasks.values():
            task.cancel()

    # ---------------------------------------------------------------- tick
    async def tick(self) -> None:
        async with session_scope() as session:
            campaigns = list(await session.scalars(
                select(Campaign).where(Campaign.state == str(CampaignState.RUNNING))))

        for campaign in campaigns:
            async with session_scope() as session:
                fresh = await session.scalar(
                    select(Campaign).where(Campaign.campaign_id == campaign.campaign_id))
                if fresh is None or fresh.state != str(CampaignState.RUNNING):
                    continue
                batch = await self._open_batch_row(session, fresh)
                if batch is None:
                    await self._open_batch(session, fresh)
                else:
                    await self._maybe_close_batch(session, fresh, batch)

    async def _open_batch_row(self, session: AsyncSession, campaign: Campaign) -> Batch | None:
        return await session.scalar(
            select(Batch)
            .where(Batch.campaign_id == campaign.campaign_id,
                   Batch.closed_at.is_(None))
            .order_by(Batch.index.desc()))

    # ------------------------------------------------------------- start --
    async def start_campaign(self, campaign_id: str) -> None:
        async with session_scope() as session:
            campaign = await session.scalar(
                select(Campaign).where(Campaign.campaign_id == campaign_id))
            if campaign is None:
                raise ValueError(f"unknown campaign {campaign_id}")
            if campaign.state not in (str(CampaignState.DRAFT), str(CampaignState.PAUSED)):
                raise ValueError(f"campaign is {campaign.state}, cannot start")

            campaign.state = str(CampaignState.RUNNING)
            campaign.started_at = campaign.started_at or _now()
            log.info("campaign_started", campaign_id=campaign_id, name=campaign.name)

    async def pause_campaign(self, campaign_id: str) -> None:
        async with session_scope() as session:
            await session.execute(
                update(Campaign).where(Campaign.campaign_id == campaign_id)
                .values(state=str(CampaignState.PAUSED)))
        log.info("campaign_paused", campaign_id=campaign_id)

    # -------------------------------------------------------- open a batch --
    async def _plan_size(self, session: AsyncSession, campaign: Campaign) -> int:
        if campaign.batches_completed == 0:
            return max(campaign.batch_size_min,
                       min(campaign.canary_size, campaign.batch_size_max))
        return campaign.current_batch_size

    async def _open_batch(self, session: AsyncSession, campaign: Campaign) -> None:
        pending = list(await session.scalars(
            select(CampaignTarget)
            .where(CampaignTarget.campaign_id == campaign.campaign_id,
                   CampaignTarget.state == str(TargetState.PENDING))
            .order_by(CampaignTarget.id)))

        if not pending:
            await self._complete_campaign(session, campaign)
            return

        planned = await self._plan_size(session, campaign)
        selected = pending[:planned]
        index = campaign.batches_completed + 1
        is_canary = campaign.batches_completed == 0

        batch = Batch(campaign_id=campaign.campaign_id, index=index,
                      planned_size=planned, actual_size=len(selected),
                      is_canary=is_canary, opened_at=_now())
        session.add(batch)
        await session.flush()

        log.info("batch_opened", campaign_id=campaign.campaign_id, batch=index,
                 planned=planned, selected=len(selected), canary=is_canary)

        firmware = await session.scalar(
            select(Firmware).where(Firmware.firmware_id == campaign.firmware_id))
        pkg = await self._package(session, campaign.firmware_id)
        key = self._signing_key()
        ingestor = Ingestor(session)

        policy = EligibilityPolicy(
            min_battery=campaign.min_battery,
            min_network_quality=campaign.min_network_quality,
            target_version_code=firmware.version_code,
            offline_ttl_seconds=settings.device_offline_ttl_seconds,
        )

        for target in selected:
            device = await session.scalar(
                select(Device).where(Device.device_id == target.device_id))
            snapshot = DeviceSnapshot(
                device_id=device.device_id, online=device.online,
                battery=device.battery, network_quality=device.network_quality,
                current_version_code=device.current_version_code or 0,
                model=device.model,
                seconds_since_last_seen=(
                    (_now() - device.last_seen_at).total_seconds()
                    if device.last_seen_at else None),
            )
            result = evaluate(snapshot, policy)
            target.batch_id = batch.id
            target.started_at = _now()

            if not result.eligible:
                # SKIPPED. Recorded with the readings that caused it, and
                # excluded from the failure rate (Rules.md §3).
                target.state = str(TargetState.SKIPPED)
                target.last_reason_code = str(result.reason)
                target.ended_at = _now()
                target.deferrals += 1
                batch.skipped_count += 1
                await ingestor.record_event(
                    device_id=device.device_id, campaign_id=campaign.campaign_id,
                    batch_id=batch.id, event_type=EventType.ELIGIBILITY_CHECKED,
                    reason_code=str(result.reason), battery=device.battery,
                    network_quality=device.network_quality, source="server",
                    payload={"detail": result.detail, "eligible": False})
                log.info("target_skipped", device_id=device.device_id,
                         reason=str(result.reason), detail=result.detail)
                continue

            manifest = build_manifest(
                pkg, device_id=device.device_id, campaign_id=campaign.campaign_id,
                min_battery=campaign.min_battery,
                min_network_quality=campaign.min_network_quality)
            signed = sign_manifest(manifest, key)

            # Publishing can fail: the broker connection may have dropped
            # between the health check and this call. Letting that exception
            # escape would abort the ENTIRE batch and roll back every offer
            # already sent in it, so one flaky publish costs fifteen devices
            # their turn. Contain it to the device it affects.
            try:
                await self.bridge.publish(
                    topics.server_topic(device.device_id, topics.ServerLeaf.OTA_OFFER),
                    signed.to_wire())
            except Exception as exc:
                target.state = str(TargetState.SKIPPED)
                target.last_reason_code = str(ReasonCode.SKIPPED_OFFLINE)
                target.deferrals += 1
                target.ended_at = _now()
                batch.skipped_count += 1
                await ingestor.record_event(
                    device_id=device.device_id, campaign_id=campaign.campaign_id,
                    batch_id=batch.id, event_type=EventType.OFFER_REJECTED,
                    reason_code=str(ReasonCode.SKIPPED_OFFLINE), source="server",
                    payload={"detail": f"offer publish failed: {exc}",
                             "deferrals": target.deferrals})
                log.warning("offer_publish_failed", device_id=device.device_id,
                            error=str(exc)[:120])
                continue

            target.state = str(TargetState.OFFERED)
            target.attempts += 1
            target.offer_nonce = manifest["nonce"]
            await ingestor.record_event(
                device_id=device.device_id, campaign_id=campaign.campaign_id,
                batch_id=batch.id, event_type=EventType.OFFER_SENT,
                battery=device.battery, network_quality=device.network_quality,
                source="server",
                payload={"version": firmware.version, "chunks": pkg.chunk_count,
                         "attempt": target.attempts})
            log.info("offer_sent", device_id=device.device_id,
                     version=firmware.version, attempt=target.attempts)

    # ------------------------------------------------ device-driven events --
    async def handle_ack(self, device_id: str, payload: dict) -> None:
        """Device accepted or rejected the offer."""
        campaign_id = payload.get("campaign_id")
        accepted = bool(payload.get("accepted"))

        async with session_scope() as session:
            target = await self._target(session, campaign_id, device_id)
            if target is None:
                return
            ingestor = Ingestor(session)

            if not accepted:
                reason = payload.get("reason_code") or str(ReasonCode.FAILED_TIMEOUT)
                await self._finish_target(session, ingestor, target, reason,
                                          payload.get("battery"),
                                          payload.get("network_quality"))
                log.warning("offer_rejected", device_id=device_id, reason=reason)
                return

            target.state = str(TargetState.DOWNLOADING)
            await ingestor.record_event(
                device_id=device_id, campaign_id=campaign_id,
                batch_id=target.batch_id, event_type=EventType.OFFER_ACCEPTED,
                payload={"nonce": payload.get("nonce")})
            campaign = await session.scalar(
                select(Campaign).where(Campaign.campaign_id == campaign_id))
            firmware_id = campaign.firmware_id
            start_at = max(0, target.last_chunk_index + 1)

        log.info("offer_accepted", device_id=device_id, start_chunk=start_at)
        self._start_stream(device_id, campaign_id, firmware_id, start_at)

    async def handle_resume(self, device_id: str, payload: dict) -> None:
        """Device reconnected mid-download and wants the rest.

        This is Requirement 11 made visible: the stream restarts at the chunk
        after the last one the device verified, not at zero.
        """
        campaign_id = payload.get("campaign_id")
        last = int(payload.get("last_chunk_index", -1))

        async with session_scope() as session:
            target = await self._target(session, campaign_id, device_id)
            if target is None:
                return
            target.last_chunk_index = last
            target.state = str(TargetState.DOWNLOADING)
            campaign = await session.scalar(
                select(Campaign).where(Campaign.campaign_id == campaign_id))
            firmware_id = campaign.firmware_id
            await Ingestor(session).record_event(
                device_id=device_id, campaign_id=campaign_id,
                batch_id=target.batch_id, event_type=EventType.DOWNLOAD_RESUMED,
                payload={"resume_from": last + 1})

        log.info("download_resumed", device_id=device_id, from_chunk=last + 1)
        self._start_stream(device_id, campaign_id, firmware_id, last + 1)

    async def handle_progress(self, device_id: str, payload: dict) -> None:
        campaign_id = payload.get("campaign_id")
        index = int(payload.get("chunk_index", -1))
        async with session_scope() as session:
            await session.execute(
                update(CampaignTarget)
                .where(CampaignTarget.campaign_id == campaign_id,
                       CampaignTarget.device_id == device_id)
                .values(last_chunk_index=index))

    async def handle_result(self, device_id: str, payload: dict) -> None:
        """Terminal outcome for one device in one attempt."""
        campaign_id = payload.get("campaign_id")
        reason = payload.get("reason_code") or str(ReasonCode.SUCCESS)

        async with session_scope() as session:
            target = await self._target(session, campaign_id, device_id)
            if target is None:
                return
            ingestor = Ingestor(session)
            await self._finish_target(
                session, ingestor, target, reason,
                payload.get("battery"), payload.get("network_quality"),
                new_version=payload.get("version"),
                chunk_index=payload.get("chunk_index"))

        emoji = "ok" if reason == str(ReasonCode.SUCCESS) else "FAILED"
        log.info("update_result", device_id=device_id, outcome=emoji, reason=reason)

    def _cancel_stream(self, campaign_id: str, device_id: str) -> None:
        """Stop pushing chunks at a device that has already finished.

        Without this the stream task runs to completion even after the device
        has reported failure -- the server keeps publishing all 32 chunks at a
        client that gave up at chunk 17. Harmless for a 256 KiB image, but at
        fleet scale it is bandwidth spent on updates that are already lost, and
        the broker charges for it.
        """
        task = self._stream_tasks.pop(f"{campaign_id}:{device_id}", None)
        if task and not task.done():
            task.cancel()

    async def _finish_target(self, session: AsyncSession, ingestor: Ingestor,
                             target: CampaignTarget, reason: str,
                             battery: int | None, network: int | None,
                             new_version: str | None = None,
                             chunk_index: int | None = None) -> None:
        code = ReasonCode(reason) if reason in ReasonCode.__members__.values() \
            else ReasonCode.FAILED_TIMEOUT
        success = code == ReasonCode.SUCCESS

        target.state = str(TargetState.SUCCEEDED if success else TargetState.FAILED)
        target.last_reason_code = str(code)
        target.ended_at = _now()
        self._cancel_stream(target.campaign_id, target.device_id)

        batch = await session.scalar(select(Batch).where(Batch.id == target.batch_id))
        if batch is not None:
            if success:
                batch.success_count += 1
            elif counts_as_failure(code):
                batch.failure_count += 1

        if success and new_version:
            await session.execute(
                update(Device).where(Device.device_id == target.device_id)
                .values(current_version=new_version))

        await ingestor.record_event(
            device_id=target.device_id, campaign_id=target.campaign_id,
            batch_id=target.batch_id,
            event_type=EventType.UPDATE_SUCCEEDED if success else EventType.UPDATE_FAILED,
            reason_code=str(code), battery=battery, network_quality=network,
            source="device",
            payload={"version": new_version, "chunk_index": chunk_index,
                     "attempt": target.attempts})

    # ------------------------------------------------------ chunk streaming --
    def _start_stream(self, device_id: str, campaign_id: str,
                      firmware_id: str, start_index: int) -> None:
        key = f"{campaign_id}:{device_id}"
        existing = self._stream_tasks.get(key)
        if existing and not existing.done():
            existing.cancel()
        self._stream_tasks[key] = asyncio.create_task(
            self._stream(device_id, campaign_id, firmware_id, start_index))

    async def _stream(self, device_id: str, campaign_id: str,
                      firmware_id: str, start_index: int) -> None:
        """Push chunks to one device.

        A fixed inter-chunk delay rather than a sliding window keyed on acks:
        it is simpler, it is bounded, and at 32 chunks the total transfer is a
        few seconds either way. The window optimisation matters at 8 MiB
        images, not at 256 KiB, and premature complexity here would be the
        hardest part of the system to debug live.
        """
        try:
            async with session_scope() as session:
                pkg = await self._package(session, firmware_id)

            topic = topics.server_topic(device_id, topics.ServerLeaf.OTA_CHUNK)
            for index in range(start_index, pkg.chunk_count):
                await self.bridge.publish(topic, pkg.chunk_payload(index, campaign_id))
                await asyncio.sleep(0.05)
            log.info("chunks_sent", device_id=device_id,
                     first=start_index, last=pkg.chunk_count - 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("chunk_stream_failed", device_id=device_id)

    # --------------------------------------------------------- close a batch --
    async def _maybe_close_batch(self, session: AsyncSession, campaign: Campaign,
                                 batch: Batch) -> None:
        targets = list(await session.scalars(
            select(CampaignTarget).where(CampaignTarget.batch_id == batch.id)))
        terminal = {str(TargetState.SUCCEEDED), str(TargetState.FAILED),
                    str(TargetState.SKIPPED), str(TargetState.ROLLED_BACK)}
        unfinished = [t for t in targets if t.state not in terminal]

        age = (_now() - batch.opened_at).total_seconds()
        timed_out = age > campaign.batch_timeout_seconds

        if unfinished and not timed_out:
            return

        ingestor = Ingestor(session)
        if unfinished:
            # The batch timeout is the safety net for devices that accepted an
            # offer and then went silent. Without it a single wedged device
            # would stall the entire campaign forever.
            for target in unfinished:
                target.state = str(TargetState.FAILED)
                target.last_reason_code = str(ReasonCode.FAILED_TIMEOUT)
                target.ended_at = _now()
                batch.failure_count += 1
                await ingestor.record_event(
                    device_id=target.device_id, campaign_id=campaign.campaign_id,
                    batch_id=batch.id, event_type=EventType.UPDATE_FAILED,
                    reason_code=str(ReasonCode.FAILED_TIMEOUT), source="server",
                    payload={"batch_age_s": round(age), "last_chunk": target.last_chunk_index})
            log.warning("batch_timed_out", campaign_id=campaign.campaign_id,
                        batch=batch.index, stalled=len(unfinished))

        outcomes = tuple(
            ReasonCode(t.last_reason_code) for t in targets
            if t.last_reason_code in ReasonCode.__members__.values())
        outcome = BatchOutcome(index=batch.index, outcomes=outcomes)

        policy = RolloutPolicy(
            batch_size_initial=campaign.batch_size_initial,
            batch_size_min=campaign.batch_size_min,
            batch_size_max=campaign.batch_size_max,
            canary_size=campaign.canary_size,
            shrink_threshold=campaign.shrink_threshold,
            abort_threshold=campaign.abort_threshold,
            grow_after_clean_batches=campaign.grow_after_clean_batches,
            ewma_alpha=campaign.ewma_alpha,
        )
        state = RolloutState(
            batch_size=campaign.current_batch_size,
            ewma_failure_rate=campaign.ewma_failure_rate,
            clean_streak=campaign.clean_streak,
            batches_completed=campaign.batches_completed,
        )

        decision = decide(outcome, policy, state)

        batch.closed_at = _now()
        campaign.current_batch_size = decision.new_batch_size
        campaign.ewma_failure_rate = decision.next_state.ewma_failure_rate
        campaign.clean_streak = decision.next_state.clean_streak
        campaign.batches_completed = decision.next_state.batches_completed

        session.add(RolloutDecision(
            campaign_id=campaign.campaign_id, batch_id=batch.id,
            batch_index=batch.index,
            prev_batch_size=decision.previous_batch_size,
            new_batch_size=decision.new_batch_size,
            observed_failure_rate=decision.observed_failure_rate,
            ewma_failure_rate=decision.ewma_failure_rate,
            attempted=decision.attempted, failures=decision.failures,
            skipped=decision.skipped, action=str(decision.action),
            reason_code=str(decision.reason), detail=decision.detail,
        ))

        # The banner. A graded success criterion, printed to stdout on purpose
        # so it is legible on a projector next to the dashboard.
        print("\n" + self._banner(decision, campaign.campaign_id, batch.index,
                                  targets) + "\n", flush=True)

        if decision.action is DecisionAction.ABORT:
            campaign.state = str(CampaignState.ABORTED)
            campaign.ended_at = _now()
            await session.execute(
                update(CampaignTarget)
                .where(CampaignTarget.campaign_id == campaign.campaign_id,
                       CampaignTarget.state == str(TargetState.PENDING))
                .values(state=str(TargetState.SKIPPED),
                        last_reason_code=str(ReasonCode.ABORTED_FAILURE_STORM)))
            log.error("campaign_aborted", campaign_id=campaign.campaign_id,
                      reason=str(decision.reason))
            return

        # Requirement 11: automatic retry. A device that failed goes back into
        # the pool for another attempt, at the NEW (usually smaller) batch size.
        #
        # Re-queuing happens only AFTER the batch has closed and the decision
        # has been recorded. Doing it earlier would remove the failure from the
        # batch before the adaptive engine counted it, and the engine would
        # never see the failure rate that justified shrinking.
        #
        # The attempt counter is the guard against a permanently broken device
        # cycling forever: tcu_D_004 at 8% battery will fail every attempt, and
        # after max_attempts it stops for good with FAILED_MAX_ATTEMPTS.
        retryable = list(await session.scalars(
            select(CampaignTarget).where(
                CampaignTarget.campaign_id == campaign.campaign_id,
                CampaignTarget.batch_id == batch.id,
                CampaignTarget.state == str(TargetState.FAILED))))
        for target in retryable:
            if target.attempts >= campaign.max_attempts:
                target.last_reason_code = str(ReasonCode.FAILED_MAX_ATTEMPTS)
                await ingestor.record_event(
                    device_id=target.device_id, campaign_id=campaign.campaign_id,
                    batch_id=batch.id, event_type=EventType.UPDATE_FAILED,
                    reason_code=str(ReasonCode.FAILED_MAX_ATTEMPTS), source="server",
                    payload={"attempts": target.attempts,
                             "max_attempts": campaign.max_attempts})
                log.warning("target_exhausted", device_id=target.device_id,
                            attempts=target.attempts)
                continue

            target.state = str(TargetState.PENDING)
            target.batch_id = None
            target.ended_at = None
            # last_chunk_index is deliberately NOT reset: the device kept its
            # verified chunks, so the retry resumes rather than restarting.
            await ingestor.record_event(
                device_id=target.device_id, campaign_id=campaign.campaign_id,
                batch_id=batch.id, event_type=EventType.RETRY_SCHEDULED,
                reason_code=target.last_reason_code, source="server",
                payload={"attempt": target.attempts,
                         "next_attempt": target.attempts + 1,
                         "resume_from_chunk": target.last_chunk_index + 1})
            log.info("retry_scheduled", device_id=target.device_id,
                     attempt=target.attempts + 1, of=campaign.max_attempts,
                     reason=target.last_reason_code)

        # Transient skips get another chance. A device passed over because it
        # was offline, or its battery was low, has not failed -- the system
        # declined to try. In a real fleet that device is a parked car, and it
        # should be offered the update on a later pass rather than written off,
        # which is what happened when a broker reconnect marked two healthy
        # devices SKIPPED_OFFLINE and the campaign completed without them.
        #
        # Bounded by max_deferrals so a permanently absent device cannot keep a
        # campaign open forever.
        deferred = list(await session.scalars(
            select(CampaignTarget).where(
                CampaignTarget.campaign_id == campaign.campaign_id,
                CampaignTarget.batch_id == batch.id,
                CampaignTarget.state == str(TargetState.SKIPPED))))
        for target in deferred:
            transient = target.last_reason_code in (
                str(ReasonCode.SKIPPED_OFFLINE),
                str(ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY),
                str(ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK),
            )
            if not transient or target.deferrals >= MAX_DEFERRALS:
                continue
            target.state = str(TargetState.PENDING)
            target.batch_id = None
            target.ended_at = None
            log.info("target_deferred", device_id=target.device_id,
                     deferral=target.deferrals, of=MAX_DEFERRALS,
                     reason=target.last_reason_code)

        # Push the re-queued rows to the database before asking whether any
        # PENDING targets remain.
        #
        # The session runs with autoflush=False (chosen so a stray SELECT can
        # never trigger a surprise write mid-handler). The cost is that ORM
        # changes live only in the identity map until flushed, while the query
        # below goes to Postgres -- so without this flush the retries are
        # invisible, `remaining` comes back None, and the campaign completes
        # having scheduled retries it then never runs.
        await session.flush()

        remaining = await session.scalar(
            select(CampaignTarget)
            .where(CampaignTarget.campaign_id == campaign.campaign_id,
                   CampaignTarget.state == str(TargetState.PENDING)))
        if remaining is None:
            await self._complete_campaign(session, campaign)

    def _banner(self, decision: Decision, campaign_id: str, batch_index: int,
                targets: list[CampaignTarget]) -> str:
        lines = [decision.banner(campaign_id, batch_index)]
        failed = [t for t in targets
                  if t.last_reason_code and t.last_reason_code.startswith("FAILED_")]
        if failed:
            detail = "  ".join(f"{t.device_id}({t.last_reason_code})" for t in failed)
            lines.append(f"           failures: {detail}")
        return "\n".join(lines)

    async def _complete_campaign(self, session: AsyncSession, campaign: Campaign) -> None:
        campaign.state = str(CampaignState.COMPLETED)
        campaign.ended_at = _now()
        log.info("campaign_completed", campaign_id=campaign.campaign_id,
                 batches=campaign.batches_completed)

    # ------------------------------------------------------------- helpers --
    async def _target(self, session: AsyncSession, campaign_id: str | None,
                      device_id: str) -> CampaignTarget | None:
        if not campaign_id:
            return None
        return await session.scalar(
            select(CampaignTarget).where(
                CampaignTarget.campaign_id == campaign_id,
                CampaignTarget.device_id == device_id))

    async def _package(self, session: AsyncSession, firmware_id: str) -> FirmwarePackage:
        if firmware_id not in self._packages:
            self._packages[firmware_id] = await load_package(session, firmware_id)
        return self._packages[firmware_id]

    def _signing_key(self):
        if self._key is None:
            self._key = load_signing_key()
        return self._key