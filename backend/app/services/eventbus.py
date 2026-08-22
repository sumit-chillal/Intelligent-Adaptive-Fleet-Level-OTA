"""
CONVOY — the live event bus.

Carries state changes from the parts of the system that produce them (the MQTT
bridge, the orchestrator) to the parts that display them (WebSocket clients),
without either side knowing the other exists.

WHY IN-PROCESS AND NOT REDIS YET
--------------------------------
Architecture.md specifies Redis pub/sub, and that is still the right answer at
scale: with several backend replicas, a device event ingested by replica 2 must
reach a browser holding a socket on replica 1, and only a shared broker can do
that. But the demo runs one process, where Redis would add a network hop, a
container, and a failure mode to solve a problem that does not exist yet.

So the INTERFACE is the one Redis would need -- publish a channel and a JSON
payload, subscribe to a channel, no assumptions about who is listening -- while
the implementation is an in-memory fan-out. Swapping in Redis later means
replacing this file, not rewriting the callers.

BACKPRESSURE
------------
Each subscriber gets a bounded queue. A browser on a slow connection, or a tab
the laptop has suspended, must not be able to make the orchestrator wait. If a
subscriber's queue is full its oldest message is dropped and a counter is
incremented: a stale frame is a far better outcome than a stalled rollout, and
the client re-syncs from the REST snapshot on reconnect anyway.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

QUEUE_LIMIT = 200


class Channel:
    """Well-known channel names. One place, so a typo cannot silently mean
    'subscribed to a channel nobody publishes to'."""

    DEVICE = "device"          # online/offline, registration, version change
    HEALTH = "health"          # battery / network samples
    EVENT = "event"            # anything written to device_events
    CAMPAIGN = "campaign"      # state transitions
    BATCH = "batch"            # opened / closed
    DECISION = "decision"      # adaptive engine output
    PROGRESS = "progress"      # per-device download progress


# eq=False keeps the default identity-based __eq__ and __hash__.
#
# A plain @dataclass generates __eq__, and Python then sets __hash__ to None,
# because two objects that compare equal must hash equal and a mutable
# dataclass cannot promise that. The result is an unhashable object, which
# cannot go in a set -- and subscribers live in a set precisely because
# identity, not field equality, is what distinguishes two browser tabs.
@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_LIMIT))
    dropped: int = 0


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._recent: deque[dict] = deque(maxlen=100)
        self.published = 0

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 50) -> list[dict]:
        """Last few messages, so a newly connected client has immediate context
        instead of an empty screen until the next event happens."""
        return list(self._recent)[-limit:]

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Fan out to every subscriber. Never blocks, never raises.

        Deliberately synchronous (not async): it is called from inside database
        transactions and MQTT handlers, and making those await a fan-out would
        couple rollout progress to how fast browsers read their sockets.
        """
        message = {
            "channel": channel,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        self._recent.append(message)
        self.published += 1

        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop the oldest, keep the newest: a dashboard cares about
                # current state, not about the frame it missed.
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                sub.dropped += 1


bus = EventBus()