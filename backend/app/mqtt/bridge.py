"""
CONVOY — the MQTT bridge.

The ONLY component that touches the broker (Rules.md R2). Everything else in
the backend reaches devices by asking the bridge to publish, and learns about
devices by reading what the bridge wrote to Postgres. That single chokepoint is
what makes the decoupling requirement structural rather than a convention
people remember to follow.

Responsibilities, and nothing else:
  1. Maintain one resilient TLS connection to the broker.
  2. Subscribe to every device topic.
  3. Validate each message, route it, and hand it to the ingestion service.
  4. Publish server -> device messages on request.
  5. Sweep for devices that stopped heartbeating.

Deliberately NOT here: campaign logic, batch decisions, eligibility rules. Those
live in the orchestrator, which never sees an MQTT packet.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import uuid
from collections import OrderedDict
from typing import Any

import aiomqtt
import structlog
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.config import settings
from app.db.session import session_scope
from app.mqtt import topics
from app.mqtt.topics import DeviceLeaf
from app.schemas.mqtt import HealthMessage, HelloMessage, StatusMessage
from app.services.ingest import Ingestor

log = structlog.get_logger(__name__)


class SeenCache:
    """Bounded LRU of recently seen msg_ids.

    The database has a unique index that guarantees correctness. This is purely
    to avoid the round trip for an obvious duplicate -- at 10,000 devices the
    difference between rejecting a duplicate in memory and rejecting it in
    Postgres is thousands of wasted queries per second.
    """

    def __init__(self, capacity: int = 20000) -> None:
        self._items: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def seen(self, msg_id: str | None) -> bool:
        if not msg_id:
            return False
        if msg_id in self._items:
            self._items.move_to_end(msg_id)
            return True
        self._items[msg_id] = None
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)
        return False


class MqttBridge:
    def __init__(self, orchestrator=None) -> None:
        # Set after construction: the orchestrator needs the bridge to publish,
        # and the bridge needs the orchestrator to route OTA messages. The
        # bridge stays usable with orchestrator=None so the transport can be
        # run and debugged on its own.
        self.orchestrator = orchestrator
        self._client: aiomqtt.Client | None = None
        self._seen = SeenCache()
        self._stop = asyncio.Event()
        self.messages_handled = 0
        self.messages_rejected = 0
        self._db_down_logged = False
        self._db_faults_logged: set[str] = set()
        self._pending_ota: list[tuple[str, str, dict]] = []
        # Set once connected, subscribed, and the fleet has been asked to
        # re-announce. The orchestrator waits on this: acting on device state
        # before we have heard from anyone means judging the fleet on stale
        # data, and every device looks offline.
        self.ready = asyncio.Event()
        self._backoff = 1

    # ------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        """Connect, subscribe, and consume forever, reconnecting on failure.

        aiomqtt raises MqttError on connection loss rather than reconnecting
        silently, which is the behaviour we want: reconnection is visible and
        logged, so a flaky venue network shows up in the logs instead of
        looking like devices going quiet.
        """
        self._backoff = 1
        while not self._stop.is_set():
            try:
                await self._session()
                self._backoff = 1
            except aiomqtt.MqttError as exc:
                # Not ready any more: a reconnect means we may have missed
                # status changes while disconnected, so the orchestrator must
                # pause and re-warm rather than act on a stale picture.
                self.ready.clear()
                log.warning("broker_connection_lost", error=str(exc),
                            retry_in_s=self._backoff)
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bridge_unexpected_error", retry_in_s=self._backoff)
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30)

    async def _session(self) -> None:
        tls = ssl.create_default_context()  # verification ON (Rules.md §6)
        client_id = f"{settings.mqtt_client_id}-{uuid.uuid4().hex[:6]}"

        async with aiomqtt.Client(
            hostname=settings.mqtt_host,
            port=settings.mqtt_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            identifier=client_id,
            tls_context=tls,
            protocol=aiomqtt.ProtocolVersion.V5,
            # 20 s rather than 30. The broker drops a client that misses its
            # keepalive window, and an idle home-router or CGNAT path will
            # silently close a TCP connection that carries no traffic. More
            # frequent pings keep the path warm and shorten the window in which
            # a dead connection goes unnoticed. Reconnection is automatic
            # either way, but a reconnect mid-campaign is worth avoiding.
            keepalive=20,
            transport="websockets" if settings.mqtt_transport == "websockets" else "tcp",
        ) as client:
            self._client = client
            use_shared = getattr(settings, "mqtt_use_shared_subscription", False)
            topic = topics.device_wildcard(shared=use_shared)
            await client.subscribe(topic, qos=1)
            log.info("bridge_connected", host=settings.mqtt_host,
                     port=settings.mqtt_port, subscribed=topic,
                     shared=use_shared, client_id=client_id)

            # Ask the whole fleet to re-announce. Without this, a backend that
            # starts after the devices has no idea what version anything runs,
            # because `hello` was published before we were listening.
            await self.announce_all()

            self.ready.set()
            # Reset the retry delay HERE, on a successful connection -- not
            # after _session() returns, because _session() only returns when
            # the bridge is shutting down. Resetting there meant every
            # reconnect doubled the delay from the previous one, so a client
            # that reconnected cleanly five times was waiting 16 s before the
            # sixth attempt despite never having failed twice in a row.
            #
            # Backoff should measure CONSECUTIVE failures. A connection that
            # succeeded and later dropped is a new incident, not a continuation
            # of the last one.
            self._backoff = 1

            async for message in client.messages:
                if self._stop.is_set():
                    break
                await self._dispatch(message)

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------- publish --
    async def publish(self, topic: str, payload: dict, *, qos: int = 1,
                      retain: bool = False) -> None:
        if self._client is None:
            raise RuntimeError("bridge is not connected")
        await self._client.publish(topic, json.dumps(payload).encode(),
                                   qos=qos, retain=retain)

    async def announce_all(self) -> None:
        await self.publish(topics.broadcast_cmd_topic(), {
            "schema": "convoy.cmd.v1",
            "msg_id": str(uuid.uuid4()),
            "cmd": "announce",
            "jitter_s": 2,
        })
        log.info("broadcast_announce_sent")

    async def ping(self, device_id: str) -> None:
        await self.publish(topics.server_topic(device_id, "cmd"), {
            "schema": "convoy.cmd.v1",
            "msg_id": str(uuid.uuid4()),
            "cmd": "ping",
            "sent_at": asyncio.get_running_loop().time(),
        })

    # ------------------------------------------------------------ dispatch --
    async def _dispatch(self, message: Any) -> None:
        topic_str = str(message.topic)
        parsed = topics.parse(topic_str)
        if parsed is None or parsed.direction != "d":
            return  # our own server->device traffic, or something unrelated

        try:
            payload = json.loads(message.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.messages_rejected += 1
            log.warning("undecodable_payload", topic=topic_str,
                        bytes=len(message.payload))
            return

        if not isinstance(payload, dict):
            self.messages_rejected += 1
            return

        # A retained status message with an empty payload is a deletion marker
        # (that is how --forget clears a retired device). Nothing to ingest.
        if not payload:
            return

        if self._seen.seen(payload.get("msg_id")):
            return

        try:
            await self._handle(parsed.device_id, parsed.leaf, payload)
            self.messages_handled += 1
            self._db_down_logged = False
            await self._drain_ota()
        except ValidationError as exc:
            self.messages_rejected += 1
            log.warning("message_failed_validation", topic=topic_str,
                        errors=exc.errors(include_url=False))
        except (OperationalError, InterfaceError) as exc:
            # The database is unreachable. One traceback per inbound message
            # would bury the actual problem under thousands of lines, so log
            # the cause once and then count silently until it recovers.
            #
            # Deliberately NOT catching DBAPIError here: ProgrammingError and
            # IntegrityError are its subclasses, and those mean the SCHEMA or
            # the QUERY is wrong, not the connection. Lumping them in produced
            # a "database_unreachable / is Postgres running?" message for a
            # missing index, which sent debugging in exactly the wrong
            # direction. An error message that misidentifies the cause is worse
            # than no error message.
            self.messages_rejected += 1
            if not self._db_down_logged:
                log.error("database_unreachable",
                          hint="is Postgres running? "
                               "cd admin && docker compose "
                               "-f docker-compose.admin.yml --env-file .env up -d",
                          error=str(exc.orig if hasattr(exc, "orig") else exc)[:200])
                self._db_down_logged = True
        except DBAPIError as exc:
            # Schema or query fault. Log it once per distinct cause, loudly and
            # accurately, without a full traceback per message.
            self.messages_rejected += 1
            cause = str(exc.orig if hasattr(exc, "orig") else exc)[:300]
            if cause not in self._db_faults_logged:
                self._db_faults_logged.add(cause)
                log.error("database_query_failed", topic=topic_str, error=cause,
                          hint="schema mismatch — check that alembic upgrade "
                               "head has been run and the migration is correct")
        except Exception:
            self.messages_rejected += 1
            log.exception("handler_failed", topic=topic_str)

    async def _handle(self, device_id: str, leaf: str, payload: dict) -> None:
        async with session_scope() as session:
            ingestor = Ingestor(session)

            if leaf == DeviceLeaf.HELLO:
                msg = HelloMessage.model_validate(payload)
                is_new = await ingestor.handle_hello(device_id, msg)
                log.info("device_hello", device_id=device_id,
                         version=msg.current_version, new=is_new,
                         fleet=msg.fleet_tag)

            elif leaf == DeviceLeaf.HEALTH:
                await ingestor.handle_health(
                    device_id, HealthMessage.model_validate(payload))

            elif leaf == DeviceLeaf.STATUS:
                msg = StatusMessage.model_validate(payload)
                await ingestor.handle_status(device_id, msg)
                log.info("device_status", device_id=device_id, online=msg.online,
                         reason=msg.reason)

            elif leaf == DeviceLeaf.PONG:
                pass  # latency probe only; nothing to persist

            elif leaf.startswith("ota/"):
                if self.orchestrator is None:
                    log.debug("ota_message_ignored", device_id=device_id, leaf=leaf,
                              reason="no orchestrator attached")
                    return
                # OTA handling runs OUTSIDE this session scope. The orchestrator
                # opens its own transactions, and nesting them here would hold a
                # connection open across chunk streaming.
                self._pending_ota.append((device_id, leaf, payload))

            else:
                log.debug("unhandled_leaf", device_id=device_id, leaf=leaf)

    async def _drain_ota(self) -> None:
        """Route queued OTA messages to the orchestrator."""
        while self._pending_ota:
            device_id, leaf, payload = self._pending_ota.pop(0)
            try:
                if leaf == DeviceLeaf.OTA_ACK:
                    await self.orchestrator.handle_ack(device_id, payload)
                elif leaf == DeviceLeaf.OTA_PROGRESS:
                    await self.orchestrator.handle_progress(device_id, payload)
                elif leaf == DeviceLeaf.OTA_RESUME:
                    await self.orchestrator.handle_resume(device_id, payload)
                elif leaf == DeviceLeaf.OTA_RESULT:
                    await self.orchestrator.handle_result(device_id, payload)
                else:
                    log.debug("unhandled_ota_leaf", device_id=device_id, leaf=leaf)
            except Exception:
                log.exception("ota_handler_failed", device_id=device_id, leaf=leaf)

    # -------------------------------------------------------------- reaper --
    async def run_offline_reaper(self, interval_s: int = 5) -> None:
        """Backstop for devices that stopped heartbeating without a last will."""
        while not self._stop.is_set():
            await asyncio.sleep(interval_s)
            try:
                async with session_scope() as session:
                    gone = await Ingestor(session).mark_stale_devices_offline(
                        settings.device_offline_ttl_seconds)
                for device_id in gone:
                    log.info("device_timed_out", device_id=device_id,
                             ttl_s=settings.device_offline_ttl_seconds)
            except (OperationalError, InterfaceError, DBAPIError):
                pass  # already reported by the dispatch path; stay quiet
            except Exception:
                log.exception("reaper_failed")