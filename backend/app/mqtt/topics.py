"""
CONVOY — MQTT topic construction and parsing.

Rules.md §4: topic strings are built here and nowhere else. Inline f-strings
scattered across handlers are how a typo in one place produces a device that
silently never receives offers, with no error anywhere.

    convoy/v1/d/{device_id}/{leaf}      device  -> server
    convoy/v1/s/{device_id}/{leaf}      server  -> device
    convoy/v1/s/all/cmd                 server  -> whole fleet (broadcast)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

ROOT = settings.mqtt_topic_root
BROADCAST_DEVICE_ID = "all"


# ------------------------------------------------------------ device -> server
class DeviceLeaf:
    HELLO = "hello"
    HEALTH = "health"
    STATUS = "status"
    PONG = "pong"
    OTA_ACK = "ota/ack"
    OTA_PROGRESS = "ota/progress"
    OTA_RESUME = "ota/resume"
    OTA_RESULT = "ota/result"
    OTA_ROLLBACK = "ota/rollback"


# ------------------------------------------------------------ server -> device
class ServerLeaf:
    CMD = "cmd"
    OTA_OFFER = "ota/offer"
    OTA_CHUNK = "ota/chunk"


def device_topic(device_id: str, leaf: str) -> str:
    return f"{ROOT}/d/{device_id}/{leaf}"


def server_topic(device_id: str, leaf: str) -> str:
    return f"{ROOT}/s/{device_id}/{leaf}"


def broadcast_cmd_topic() -> str:
    return f"{ROOT}/s/{BROADCAST_DEVICE_ID}/{ServerLeaf.CMD}"


def device_wildcard(shared: bool = False, group: str | None = None) -> str:
    """Everything published by any device.

    A SHARED subscription ($share/<group>/<filter>) makes the broker
    load-balance matching messages across all subscribers in the group, which
    is how you add backend replicas without every replica processing every
    message. It is an MQTT 5 feature and not every broker tier supports it, so
    it is opt-in via MQTT_USE_SHARED_SUBSCRIPTION and defaults to OFF. The
    plain wildcard works everywhere; the shared form is the scale path.
    """
    base = f"{ROOT}/d/+/#"
    if shared:
        return f"$share/{group or settings.mqtt_shared_group}/{base}"
    return base


@dataclass(frozen=True)
class ParsedTopic:
    device_id: str
    leaf: str
    direction: str  # "d" (from device) or "s" (to device)


def parse(topic: str) -> ParsedTopic | None:
    """convoy/v1/d/tcu_B_001/ota/progress -> (tcu_B_001, 'ota/progress', 'd')

    Returns None for anything that does not match, rather than raising: the
    bridge subscribes to a wildcard and must not die because something
    unexpected appeared on the broker.
    """
    parts = topic.split("/")
    root_parts = ROOT.split("/")
    n = len(root_parts)
    if len(parts) < n + 3 or parts[:n] != root_parts:
        return None
    direction = parts[n]
    if direction not in ("d", "s"):
        return None
    device_id = parts[n + 1]
    leaf = "/".join(parts[n + 2:])
    if not device_id or not leaf:
        return None
    return ParsedTopic(device_id=device_id, leaf=leaf, direction=direction)
