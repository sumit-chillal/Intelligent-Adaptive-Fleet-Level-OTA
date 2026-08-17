#!/usr/bin/env python3
"""
CONVOY — bridge entrypoint.

Run this on the admin machine (Laptop A) alongside Postgres and Redis:

    cd ~/Documents/Major-Project/convoy/backend
    python run_bridge.py

It replaces link_check.py from Phase 1. Same job -- watch the fleet -- except
now everything it sees is persisted, deduplicated, and auditable rather than
printed and forgotten.

Verify it is working, from another terminal:

    docker exec -it convoy_postgres psql -U convoy -d convoy \\
      -c "SELECT device_id, current_version, battery, network_quality, online
          FROM devices ORDER BY device_id;"

    docker exec -it convoy_postgres psql -U convoy -d convoy \\
      -c "SELECT ts, device_id, event_type, reason_code
          FROM device_events ORDER BY id DESC LIMIT 20;"
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog
from sqlalchemy import func, select

from app.config import settings
from app.db.models import Device, DeviceEvent
from app.db.session import check_connection, dispose_engine, session_scope
from app.core.orchestrator import Orchestrator
from app.mqtt.bridge import MqttBridge


def configure_logging() -> None:
    """Human-readable in development, JSON in deployment.

    JSON logs are machine-parseable, which matters once logs go to a hosted
    collector. On the projector during a demo, they are unreadable -- so the
    renderer is chosen by environment rather than fixed.
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout,
                        level=settings.log_level.upper())
    renderer = (structlog.dev.ConsoleRenderer(colors=True)
                if settings.convoy_env == "local"
                else structlog.processors.JSONRenderer())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("run_bridge")


async def fleet_summary(interval_s: int = 15) -> None:
    """Periodic roster, read from Postgres rather than from memory.

    Reading it back from the database is the point: it proves the data is
    actually persisted and queryable, not just held in a process that dies
    when you close the terminal.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            async with session_scope() as session:
                rows = (await session.execute(
                    select(Device.device_id, Device.device_type,
                           Device.current_version, Device.battery,
                           Device.network_quality, Device.online,
                           Device.fleet_tag)
                    .order_by(Device.device_id)
                )).all()
                event_count = await session.scalar(
                    select(func.count()).select_from(DeviceEvent))

            if not rows:
                continue

            online = sum(1 for r in rows if r.online)
            print(f"\n  ── FLEET (from postgres) ─────────────────────────────")
            print(f"  {'DEVICE':<16}{'FLEET':<10}{'VER':<9}"
                  f"{'BATT':>6}{'NET':>5}  STATE")
            for r in rows:
                print(f"  {r.device_id:<16}{str(r.fleet_tag or '-'):<10}"
                      f"{str(r.current_version or '-'):<9}"
                      f"{str(r.battery if r.battery is not None else '-'):>5}%"
                      f"{str(r.network_quality or '-'):>5}"
                      f"  {'online' if r.online else 'OFFLINE'}")
            print(f"  online={online}/{len(rows)}   events_logged={event_count}")
            print("  " + "─" * 54 + "\n")
        except Exception:
            log.exception("summary_failed")


async def main() -> int:
    configure_logging()
    log.info("bridge_starting", env=settings.convoy_env,
             broker=settings.mqtt_host, db=settings.database_url.split("@")[-1])

    # Preflight: a backend that cannot reach its database has nothing useful
    # to do. Fail here with one actionable line rather than a stack trace per
    # inbound message.
    ok, detail = await check_connection()
    if not ok:
        log.error("database_unreachable", error=detail)
        print("\n  Cannot reach Postgres.\n"
              "  Start it first:\n"
              "      cd ~/Documents/Major-Project/convoy/admin\n"
              "      docker compose -f docker-compose.admin.yml --env-file .env up -d\n"
              "      docker compose -f docker-compose.admin.yml --env-file .env ps\n"
              "  Both containers must report healthy, then re-run this.\n")
        return 1
    log.info("database_ok")

    bridge = MqttBridge()
    orchestrator = Orchestrator(bridge)
    # Circular by nature: the orchestrator publishes through the bridge, and
    # the bridge routes device replies to the orchestrator. Wiring it after
    # construction keeps each usable alone -- the bridge runs fine with no
    # orchestrator, which is how Phase 2B-2 was debugged.
    bridge.orchestrator = orchestrator

    tasks = [
        asyncio.create_task(bridge.run(), name="bridge"),
        asyncio.create_task(bridge.run_offline_reaper(), name="reaper"),
        asyncio.create_task(orchestrator.run(), name="orchestrator"),
        asyncio.create_task(fleet_summary(), name="summary"),
    ]

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    await stopping.wait()
    log.info("shutdown_requested",
             handled=bridge.messages_handled, rejected=bridge.messages_rejected)

    await bridge.stop()
    await orchestrator.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await dispose_engine()
    log.info("shutdown_complete")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)