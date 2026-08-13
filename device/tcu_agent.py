#!/usr/bin/env python3
"""
CONVOY — simulated TCU agent  (runs on Laptop B / C / D, inside Docker)

This is the device-side connection layer. One process = one TCU. Identity and
health profile come ENTIRELY from environment variables, so a laptop can run
five independent devices from one image with no source edit and no rebuild
(project requirement 16).

Credentials are also environment-only. Nothing in this file is hardcoded
except protocol constants. The battery and network-quality FAILURE PROFILES
are configuration, not secrets -- those are meant to be set per container,
which is exactly how the demo produces its deliberate failures.

Topics (see docs/protocol.md)
    OUT  convoy/v1/d/{id}/hello     registration
    OUT  convoy/v1/d/{id}/health    battery + network, every HEARTBEAT_SECONDS
    OUT  convoy/v1/d/{id}/status    retained; LWT flips it to offline
    OUT  convoy/v1/d/{id}/pong      reply to a server ping
    IN   convoy/v1/s/{id}/cmd       ping | set-config | (later: ota commands)

The OTA offer/chunk/resume handlers land in this same agent in the next phase;
this file is deliberately kept to the transport handshake so it can be proven
working across networks before any feature code is written.
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

# ----------------------------------------------------------------- config ---
# Required. Fail loudly rather than start a device with a guessed identity.
def required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        sys.exit(f"FATAL: environment variable {name} is required but not set")

    # `docker run --env-file` does not strip inline comments, quotes, or
    # trailing whitespace -- it takes everything after the '=' literally.
    # A password with a stray quote produces a bare "Not authorized" from the
    # broker, which sends you hunting for a credential problem that does not
    # exist. Catch it here instead, before the connection is even attempted.
    stripped = val.strip()
    if stripped != val:
        sys.exit(f"FATAL: {name} has leading/trailing whitespace: {val!r}\n"
                 f"       Remove the spaces in your .env file.")
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        sys.exit(f"FATAL: {name} is wrapped in quotes: {val!r}\n"
                 f"       .env values must be unquoted -- the quotes become "
                 f"part of the value.")
    if " #" in val:
        sys.exit(f"FATAL: {name} appears to contain an inline comment: {val!r}\n"
                 f"       Move the comment to its own line in your .env file.")
    return val


DEVICE_ID = required("DEVICE_ID")
MQTT_HOST = required("MQTT_HOST")
MQTT_USERNAME = required("MQTT_USERNAME")
MQTT_PASSWORD = required("MQTT_PASSWORD")

MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_TRANSPORT = os.getenv("MQTT_TRANSPORT", "tcp")
TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "convoy/v1")

DEVICE_TYPE = os.getenv("DEVICE_TYPE", "tcu-sim")
DEVICE_MODEL = os.getenv("DEVICE_MODEL", "tcu-sim-v1")
HW_REV = os.getenv("HW_REV", "A1")
FLEET_TAG = os.getenv("FLEET_TAG", "unassigned")      # e.g. laptopB

# Health profile -- deliberately configurable, this is how failures are staged.
BATTERY_LEVEL = int(os.getenv("BATTERY_LEVEL", "85"))
NETWORK_QUALITY = int(os.getenv("NETWORK_QUALITY", "5"))
BATTERY_DRAIN_PER_MIN = float(os.getenv("BATTERY_DRAIN_PER_MIN", "0"))
BATTERY_JITTER = int(os.getenv("BATTERY_JITTER", "1"))
NETWORK_JITTER = int(os.getenv("NETWORK_JITTER", "0"))

# Failure injection -- consumed by the OTA phase, declared here so the profile
# is visible in `hello` and on the dashboard from the moment a device connects.
FAILURE_MODE = os.getenv("FAILURE_MODE", "none")
FAILURE_PROBABILITY = float(os.getenv("FAILURE_PROBABILITY", "0"))

HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "5"))
STATE_DIR = Path(os.getenv("STATE_DIR", "/data"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=LOG_LEVEL,
    format=f"%(asctime)s %(levelname)-5s [{DEVICE_ID}] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tcu")

T_HELLO = f"{TOPIC_ROOT}/d/{DEVICE_ID}/hello"
T_HEALTH = f"{TOPIC_ROOT}/d/{DEVICE_ID}/health"
T_STATUS = f"{TOPIC_ROOT}/d/{DEVICE_ID}/status"
T_PONG = f"{TOPIC_ROOT}/d/{DEVICE_ID}/pong"
T_CMD = f"{TOPIC_ROOT}/s/{DEVICE_ID}/cmd"

# ------------------------------------------------------------- local state --
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / f"{DEVICE_ID}.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("state file corrupt, starting fresh")
    return {
        "current_version": os.getenv("INITIAL_VERSION", "1.3.0"),
        "active_slot": "A",
        "previous_version": None,
        "resume": None,          # {campaign_id, firmware_id, next_chunk, ...}
        "min_allowed_version_code": 0,
    }


def save_state(state: dict) -> bool:
    """Atomically persist resume state. Returns False if the volume is unwritable.

    Write-to-temp-then-rename means a kill mid-write can never leave a
    half-written state file, which is what makes chunk-level resume reliable.

    A failure here is serious -- it means this device cannot resume an
    interrupted download -- but it must not crash the process, because that
    would turn a recoverable storage problem into a dead device.
    """
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)
        return True
    except OSError as exc:
        log.error("CANNOT PERSIST STATE to %s (%s). Resume-after-interruption "
                  "will not work for this device. Check volume permissions.",
                  STATE_FILE, exc)
        return False


state = load_state()
battery = float(BATTERY_LEVEL)
started_at = time.time()
stop = threading.Event()


def envelope(**fields) -> str:
    return json.dumps({
        "schema": fields.pop("schema"),
        "msg_id": str(uuid.uuid4()),
        "device_id": DEVICE_ID,
        "ts": time.time(),
        **fields,
    })


# ------------------------------------------------------------- mqtt handlers -
def on_connect(client, _userdata, _flags, rc, properties=None):
    if rc != 0:
        log.error("broker refused connection rc=%s — check MQTT_USERNAME/PASSWORD", rc)
        return
    log.info("connected to %s:%s", MQTT_HOST, MQTT_PORT)
    client.subscribe(T_CMD, qos=1)

    client.publish(T_STATUS, envelope(schema="convoy.status.v1", online=True),
                   qos=1, retain=True)
    client.publish(T_HELLO, envelope(
        schema="convoy.hello.v1",
        device_type=DEVICE_TYPE,
        model=DEVICE_MODEL,
        hw_rev=HW_REV,
        fleet_tag=FLEET_TAG,
        current_version=state["current_version"],
        active_slot=state["active_slot"],
        battery=round(battery),
        network_quality=NETWORK_QUALITY,
        failure_profile={"mode": FAILURE_MODE, "p": FAILURE_PROBABILITY},
        resume_pending=state.get("resume") is not None,
        agent="tcu-agent/0.1",
    ), qos=1)
    log.info("announced v%s battery=%d%% net=%d profile=%s",
             state["current_version"], round(battery), NETWORK_QUALITY, FAILURE_MODE)


def on_disconnect(_client, _userdata, rc, properties=None, reason=None):
    log.warning("disconnected rc=%s — paho will retry with backoff", rc)


def on_message(client, _userdata, msg: mqtt.MQTTMessage):
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.warning("undecodable message on %s", msg.topic)
        return

    cmd = payload.get("cmd")
    if cmd == "ping":
        client.publish(T_PONG, envelope(schema="convoy.pong.v1",
                                        sent_at=payload.get("sent_at")), qos=1)
    elif cmd == "set-config":
        # Runtime health override, used to stage a failure live on stage.
        global battery, NETWORK_QUALITY
        if "battery" in payload:
            battery = float(payload["battery"])
        if "network_quality" in payload:
            NETWORK_QUALITY = int(payload["network_quality"])
        log.info("config updated battery=%d net=%d", round(battery), NETWORK_QUALITY)
    else:
        log.debug("unhandled cmd=%s (OTA commands land in the next phase)", cmd)


# ---------------------------------------------------------------- heartbeat --
def heartbeat(client: mqtt.Client) -> None:
    global battery
    while not stop.wait(HEARTBEAT_SECONDS):
        if BATTERY_DRAIN_PER_MIN:
            battery = max(0.0, battery - BATTERY_DRAIN_PER_MIN * HEARTBEAT_SECONDS / 60)
        reported_batt = max(0, min(100, round(battery) +
                                   random.randint(-BATTERY_JITTER, BATTERY_JITTER)))
        reported_net = max(1, min(5, NETWORK_QUALITY +
                                  random.randint(-NETWORK_JITTER, NETWORK_JITTER)))
        client.publish(T_HEALTH, envelope(
            schema="convoy.health.v1",
            battery=reported_batt,
            network_quality=reported_net,
            uptime_s=int(time.time() - started_at),
            current_version=state["current_version"],
        ), qos=1)


def shutdown(signum, _frame):
    log.info("signal %s — going offline cleanly", signum)
    stop.set()


def main() -> int:
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{DEVICE_ID}-{uuid.uuid4().hex[:6]}",
        protocol=mqtt.MQTTv5,
        transport="websockets" if MQTT_TRANSPORT == "websockets" else "tcp",
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)

    # Last Will: if this container dies or the network drops, the broker
    # publishes this for us and the dashboard shows the device offline within
    # the keepalive window. No polling required.
    client.will_set(T_STATUS,
                    json.dumps({"schema": "convoy.status.v1", "device_id": DEVICE_ID,
                                "online": False, "reason": "last_will"}),
                    qos=1, retain=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    log.info("dialling broker %s:%s (transport=%s)", MQTT_HOST, MQTT_PORT, MQTT_TRANSPORT)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=15)
    client.loop_start()

    threading.Thread(target=heartbeat, args=(client,), daemon=True).start()
    stop.wait()

    client.publish(T_STATUS, envelope(schema="convoy.status.v1", online=False,
                                      reason="clean_shutdown"), qos=1, retain=True)
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())