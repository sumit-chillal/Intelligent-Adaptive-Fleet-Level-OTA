"""
CONVOY — the API server.

One process runs everything: FastAPI, the MQTT bridge, and the orchestrator.

WHY ONE PROCESS
---------------
The dashboard needs sub-100 ms updates, and the shortest path from "a device
reported failure" to "a browser repaints" is an in-memory function call. Split
across processes, the same path needs a message broker in the middle, which is
exactly the Redis hop that Architecture.md defers to the scale story. Since the
demo has one backend, one process is both faster and simpler.

The components stay separable: the bridge knows nothing about HTTP, the
orchestrator knows nothing about WebSockets, and they communicate only through
Postgres and the event bus. Splitting them later means changing startup code,
not logic.

    uvicorn app.main:app --reload --port 8000
    open http://localhost:8000/docs

The docs page is worth knowing about: FastAPI generates it from the route
signatures, so it is a working API console with no extra effort -- useful for
demonstrating the backend before the dashboard exists.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import CampaignState, TargetState
from app.core.orchestrator import Orchestrator
from app.db.models import (
    Batch,
    Campaign,
    CampaignTarget,
    Device,
    DeviceEvent,
    DeviceHealthSample,
    Firmware,
    RolloutDecision,
)
from app.db.session import check_connection, dispose_engine, get_session
from app.mqtt.bridge import MqttBridge
from app.services.eventbus import Channel, bus


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout,
                        level=settings.log_level.upper())
    renderer = (structlog.dev.ConsoleRenderer(colors=True)
                if settings.convoy_env == "local"
                else structlog.processors.JSONRenderer())
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars,
                    structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="%H:%M:%S"),
                    renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("api")
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    ok, detail = await check_connection()
    if not ok:
        log.error("database_unreachable", error=detail)
        raise RuntimeError(
            "Cannot reach Postgres. Start it: cd admin && docker compose "
            "-f docker-compose.admin.yml --env-file .env up -d")
    log.info("database_ok")

    bridge = MqttBridge()
    orchestrator = Orchestrator(bridge)
    bridge.orchestrator = orchestrator
    state["bridge"] = bridge
    state["orchestrator"] = orchestrator
    state["tasks"] = [
        asyncio.create_task(bridge.run(), name="bridge"),
        asyncio.create_task(bridge.run_offline_reaper(), name="reaper"),
        asyncio.create_task(orchestrator.run(), name="orchestrator"),
    ]
    log.info("api_started", port=settings.api_port)

    yield

    await bridge.stop()
    await orchestrator.stop()
    for task in state["tasks"]:
        task.cancel()
    await asyncio.gather(*state["tasks"], return_exceptions=True)
    await dispose_engine()
    log.info("api_stopped")


app = FastAPI(title="CONVOY OTA", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(x_admin_token: str = Header(default="")) -> None:
    """Guard for anything that changes state.

    Read endpoints are open so the dashboard needs no auth to display the
    fleet, but starting, pausing or aborting a rollout is an action with real
    consequences and is gated even in the demo build (Rules.md §6).
    """
    if not settings.admin_api_token:
        return  # not configured; local development only
    if x_admin_token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Admin-Token")


# ------------------------------------------------------------------ health --
@app.get("/api/health")
async def health() -> dict:
    bridge: MqttBridge = state.get("bridge")
    return {
        "status": "ok",
        "broker_connected": bool(bridge and bridge.ready.is_set()),
        "messages_handled": bridge.messages_handled if bridge else 0,
        "messages_rejected": bridge.messages_rejected if bridge else 0,
        "ws_subscribers": bus.subscriber_count,
        "events_published": bus.published,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ----------------------------------------------------------------- devices --
@app.get("/api/devices")
async def list_devices(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = list(await session.scalars(select(Device).order_by(Device.device_id)))
    return [_device_dict(d) for d in rows]


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str,
                     session: AsyncSession = Depends(get_session)) -> dict:
    device = await session.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
        raise HTTPException(404, f"unknown device {device_id}")

    samples = list(await session.scalars(
        select(DeviceHealthSample)
        .where(DeviceHealthSample.device_id == device_id)
        .order_by(desc(DeviceHealthSample.ts)).limit(60)))
    events = list(await session.scalars(
        select(DeviceEvent).where(DeviceEvent.device_id == device_id)
        .order_by(desc(DeviceEvent.id)).limit(50)))

    return {
        **_device_dict(device),
        # Reversed so the sparkline reads left-to-right in time order.
        "health": [{"battery": s.battery, "network_quality": s.network_quality,
                    "ts": s.ts.isoformat()} for s in reversed(samples)],
        "events": [_event_dict(e) for e in events],
    }


@app.get("/api/devices/{device_id}/timeline")
async def device_timeline(device_id: str, campaign_id: str | None = None,
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Requirement 14, as an endpoint.

    'What happened to device X during campaign Y and why' -- ordered by the
    server-assigned id, never by a device timestamp, because fifteen machines
    in three cities disagree about the time.
    """
    stmt = select(DeviceEvent).where(DeviceEvent.device_id == device_id)
    if campaign_id:
        stmt = stmt.where(DeviceEvent.campaign_id == campaign_id)
    rows = list(await session.scalars(stmt.order_by(DeviceEvent.id)))
    return [_event_dict(e) for e in rows]


# ---------------------------------------------------------------- firmware --
@app.get("/api/firmware")
async def list_firmware(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = list(await session.scalars(
        select(Firmware).order_by(desc(Firmware.created_at))))
    return [{"firmware_id": f.firmware_id, "version": f.version,
             "version_code": f.version_code, "model": f.model,
             "size_bytes": f.size_bytes, "sha256": f.sha256,
             "chunk_count": f.chunk_count, "chunk_size": f.chunk_size,
             "state": f.state, "notes": f.notes,
             "created_at": f.created_at.isoformat()} for f in rows]


# --------------------------------------------------------------- campaigns --
@app.get("/api/campaigns")
async def list_campaigns(session: AsyncSession = Depends(get_session)) -> list[dict]:
    campaigns = list(await session.scalars(
        select(Campaign).order_by(desc(Campaign.created_at))))
    out = []
    for c in campaigns:
        counts = dict((await session.execute(
            select(CampaignTarget.state, func.count())
            .where(CampaignTarget.campaign_id == c.campaign_id)
            .group_by(CampaignTarget.state))).all())
        out.append({**_campaign_dict(c), "counts": counts})
    return out


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str,
                       session: AsyncSession = Depends(get_session)) -> dict:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.campaign_id == campaign_id))
    if campaign is None:
        raise HTTPException(404, f"unknown campaign {campaign_id}")

    targets = list(await session.scalars(
        select(CampaignTarget).where(CampaignTarget.campaign_id == campaign_id)
        .order_by(CampaignTarget.id)))
    batches = list(await session.scalars(
        select(Batch).where(Batch.campaign_id == campaign_id).order_by(Batch.index)))
    decisions = list(await session.scalars(
        select(RolloutDecision).where(RolloutDecision.campaign_id == campaign_id)
        .order_by(RolloutDecision.batch_index)))

    return {
        **_campaign_dict(campaign),
        "targets": [{"device_id": t.device_id, "state": t.state,
                     "reason_code": t.last_reason_code, "attempts": t.attempts,
                     "deferrals": t.deferrals, "batch_id": t.batch_id,
                     "from_version": t.from_version, "to_version": t.to_version,
                     "last_chunk_index": t.last_chunk_index} for t in targets],
        "batches": [{"id": b.id, "index": b.index, "planned_size": b.planned_size,
                     "actual_size": b.actual_size, "is_canary": b.is_canary,
                     "success": b.success_count, "failure": b.failure_count,
                     "skipped": b.skipped_count,
                     "opened_at": b.opened_at.isoformat(),
                     "closed_at": b.closed_at.isoformat() if b.closed_at else None}
                    for b in batches],
        # This list IS the batch-size chart on the analytics page.
        "decisions": [{"batch_index": d.batch_index,
                       "prev_batch_size": d.prev_batch_size,
                       "new_batch_size": d.new_batch_size,
                       "observed_failure_rate": d.observed_failure_rate,
                       "ewma": d.ewma_failure_rate, "attempted": d.attempted,
                       "failures": d.failures, "skipped": d.skipped,
                       "action": d.action, "reason_code": d.reason_code,
                       "detail": d.detail, "ts": d.ts.isoformat()}
                      for d in decisions],
    }


@app.get("/api/campaigns/{campaign_id}/analytics")
async def campaign_analytics(campaign_id: str,
                             session: AsyncSession = Depends(get_session)) -> dict:
    """Failure taxonomy and outcome counts for the analytics page."""
    taxonomy = dict((await session.execute(
        select(CampaignTarget.last_reason_code, func.count())
        .where(CampaignTarget.campaign_id == campaign_id,
               CampaignTarget.last_reason_code.isnot(None))
        .group_by(CampaignTarget.last_reason_code))).all())
    states = dict((await session.execute(
        select(CampaignTarget.state, func.count())
        .where(CampaignTarget.campaign_id == campaign_id)
        .group_by(CampaignTarget.state))).all())
    return {
        "campaign_id": campaign_id,
        "by_state": states,
        "by_reason": taxonomy,
        "succeeded": states.get(str(TargetState.SUCCEEDED), 0),
        "failed": states.get(str(TargetState.FAILED), 0),
        "skipped": states.get(str(TargetState.SKIPPED), 0),
        "pending": states.get(str(TargetState.PENDING), 0),
    }


@app.post("/api/campaigns/{campaign_id}/start", dependencies=[Depends(require_admin)])
async def start_campaign(campaign_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.campaign_id == campaign_id))
    if campaign is None:
        raise HTTPException(404, f"unknown campaign {campaign_id}")
    if campaign.state not in (str(CampaignState.DRAFT), str(CampaignState.PAUSED)):
        raise HTTPException(409, f"campaign is {campaign.state}; only DRAFT or "
                                 f"PAUSED can be started")
    campaign.state = str(CampaignState.RUNNING)
    # Stamp the first start only. Resuming a paused campaign must not reset
    # the clock, or the recorded duration becomes "time since last resume"
    # rather than how long the rollout actually took.
    campaign.started_at = campaign.started_at or datetime.now(timezone.utc)
    bus.publish(Channel.CAMPAIGN, {"campaign_id": campaign_id,
                                   "state": campaign.state})
    return {"campaign_id": campaign_id, "state": campaign.state,
            "started_at": campaign.started_at.isoformat()}


@app.post("/api/campaigns/{campaign_id}/pause", dependencies=[Depends(require_admin)])
async def pause_campaign(campaign_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.campaign_id == campaign_id))
    if campaign is None:
        raise HTTPException(404, f"unknown campaign {campaign_id}")
    campaign.state = str(CampaignState.PAUSED)
    bus.publish(Channel.CAMPAIGN, {"campaign_id": campaign_id,
                                   "state": campaign.state})
    return {"campaign_id": campaign_id, "state": campaign.state}


@app.post("/api/campaigns/{campaign_id}/abort", dependencies=[Depends(require_admin)])
async def abort_campaign(campaign_id: str,
                         session: AsyncSession = Depends(get_session)) -> dict:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.campaign_id == campaign_id))
    if campaign is None:
        raise HTTPException(404, f"unknown campaign {campaign_id}")
    campaign.state = str(CampaignState.ABORTED)
    campaign.ended_at = datetime.now(timezone.utc)
    bus.publish(Channel.CAMPAIGN, {"campaign_id": campaign_id,
                                   "state": campaign.state,
                                   "reason": "ABORTED_BY_OPERATOR"})
    return {"campaign_id": campaign_id, "state": campaign.state}


# ------------------------------------------------------------------ events --
@app.get("/api/events")
async def recent_events(limit: int = Query(100, le=500),
                        session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = list(await session.scalars(
        select(DeviceEvent).order_by(desc(DeviceEvent.id)).limit(limit)))
    return [_event_dict(e) for e in rows]


# --------------------------------------------------------------- websocket --
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Live delta stream.

    The contract is snapshot-then-stream: the client fetches REST state first,
    then applies deltas from here. That is what makes a dropped socket
    recoverable -- on reconnect the client re-fetches and resumes, instead of
    trying to replay everything it missed.
    """
    await ws.accept()
    sub = bus.subscribe()
    log.info("ws_connected", subscribers=bus.subscriber_count)
    try:
        await ws.send_json({"channel": "hello", "data": {
            "recent": bus.recent(20),
            "broker_connected": bool(state.get("bridge")
                                     and state["bridge"].ready.is_set()),
        }})
        while True:
            message = await sub.queue.get()
            await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws_error")
    finally:
        bus.unsubscribe(sub)
        log.info("ws_disconnected", dropped=sub.dropped,
                 subscribers=bus.subscriber_count)


# ----------------------------------------------------------------- helpers --
def _device_dict(d: Device) -> dict:
    return {
        "device_id": d.device_id, "device_type": d.device_type, "model": d.model,
        "fleet_tag": d.fleet_tag, "current_version": d.current_version,
        "online": d.online, "battery": d.battery,
        "network_quality": d.network_quality,
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "failure_profile": d.failure_profile,
    }


def _event_dict(e: DeviceEvent) -> dict:
    return {
        "id": e.id, "device_id": e.device_id, "campaign_id": e.campaign_id,
        "event_type": e.event_type, "reason_code": e.reason_code,
        "battery": e.battery_at_event, "network_quality": e.network_at_event,
        "payload": e.payload, "source": e.source, "ts": e.ts.isoformat(),
    }


def _campaign_dict(c: Campaign) -> dict:
    return {
        "campaign_id": c.campaign_id, "name": c.name, "state": c.state,
        "firmware_id": c.firmware_id,
        "batch_size_initial": c.batch_size_initial,
        "current_batch_size": c.current_batch_size,
        "batch_size_min": c.batch_size_min, "batch_size_max": c.batch_size_max,
        "canary_size": c.canary_size, "batches_completed": c.batches_completed,
        "ewma_failure_rate": c.ewma_failure_rate,
        "shrink_threshold": c.shrink_threshold,
        "abort_threshold": c.abort_threshold,
        "min_battery": c.min_battery,
        "min_network_quality": c.min_network_quality,
        "max_attempts": c.max_attempts,
        "created_at": c.created_at.isoformat(),
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
    }