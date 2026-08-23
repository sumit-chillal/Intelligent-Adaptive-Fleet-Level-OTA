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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import ota
from ota import Download, FailureInjector, ManifestRejected, ReasonCode

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

# The server's Ed25519 PUBLIC key, 64 hex chars. Safe to distribute -- it can
# only verify signatures, never create them. Printed by tools/keygen.py.
SERVER_PUBLIC_KEY_HEX = os.getenv("SERVER_PUBLIC_KEY_HEX", "")

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

# Broadcast command channel. Every device subscribes to this in addition to its
# own. The server uses it to ask the whole fleet to re-announce itself after a
# server restart -- without it, a backend that starts up after the devices has
# no idea what firmware version anything is running, because `hello` is a
# one-shot message that was published before the server was listening.
# This is the same "desired vs reported state re-sync" pattern used by AWS IoT
# device shadows and Azure IoT twins.
T_CMD_ALL = f"{TOPIC_ROOT}/s/all/cmd"

T_OTA_OFFER = f"{TOPIC_ROOT}/s/{DEVICE_ID}/ota/offer"
T_OTA_CHUNK = f"{TOPIC_ROOT}/s/{DEVICE_ID}/ota/chunk"
T_OTA_ACK = f"{TOPIC_ROOT}/d/{DEVICE_ID}/ota/ack"
T_OTA_PROGRESS = f"{TOPIC_ROOT}/d/{DEVICE_ID}/ota/progress"
T_OTA_RESUME = f"{TOPIC_ROOT}/d/{DEVICE_ID}/ota/resume"
T_OTA_RESULT = f"{TOPIC_ROOT}/d/{DEVICE_ID}/ota/result"

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
def publish_hello(client: mqtt.Client, trigger: str) -> None:
    """Announce identity, capabilities, and current firmware version."""
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
        trigger=trigger,
    ), qos=1)
    log.info("announced v%s battery=%d%% net=%d profile=%s (trigger=%s)",
             state["current_version"], round(battery), NETWORK_QUALITY,
             FAILURE_MODE, trigger)


def on_connect(client, _userdata, _flags, rc, properties=None):
    if rc != 0:
        log.error("broker refused connection rc=%s — check MQTT_USERNAME/PASSWORD", rc)
        return
    log.info("connected to %s:%s", MQTT_HOST, MQTT_PORT)
    client.subscribe(T_CMD, qos=1)
    client.subscribe(T_CMD_ALL, qos=1)
    client.subscribe(T_OTA_OFFER, qos=1)
    client.subscribe(T_OTA_CHUNK, qos=1)

    # If a download was interrupted -- container killed, power lost, network
    # dropped -- ask the server to continue from the last verified chunk rather
    # than starting over. This is Requirement 11, and the resume state survived
    # in the state file on the mounted volume.
    resume = state.get("resume")
    if resume:
        log.info("resuming interrupted download from chunk %d",
                 resume["next_chunk"])
        client.publish(T_OTA_RESUME, envelope(
            schema="convoy.resume.v1",
            campaign_id=resume["campaign_id"],
            firmware_id=resume.get("firmware_id"),
            last_chunk_index=resume["next_chunk"] - 1,
        ), qos=1)

    client.publish(T_STATUS, envelope(schema="convoy.status.v1", online=True),
                   qos=1, retain=True)
    publish_hello(client, trigger="connect")


def on_disconnect(_client, _userdata, rc, properties=None, reason=None):
    log.warning("disconnected rc=%s — paho will retry with backoff", rc)


def on_message(client, _userdata, msg: mqtt.MQTTMessage):
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.warning("undecodable message on %s", msg.topic)
        return

    topic = msg.topic if isinstance(msg.topic, str) else str(msg.topic)
    if topic == T_OTA_OFFER:
        handle_offer(client, payload)
        return
    if topic == T_OTA_CHUNK:
        handle_chunk(client, payload)
        return

    cmd = payload.get("cmd")
    if cmd == "ping":
        client.publish(T_PONG, envelope(schema="convoy.pong.v1",
                                        sent_at=payload.get("sent_at")), qos=1)
    elif cmd == "announce":
        # The server has (re)started and is rebuilding its picture of the fleet.
        # Jitter the reply so 10,000 devices don't answer in the same millisecond
        # and stampede the broker -- the classic thundering-herd failure mode.
        time.sleep(random.uniform(0, float(payload.get("jitter_s", 2))))
        publish_hello(client, trigger="announce")
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


# --------------------------------------------------------------------- OTA --
active: Download | None = None
injector: FailureInjector | None = None
is_rollback: bool = False


def server_public_key() -> Ed25519PublicKey:
    if not SERVER_PUBLIC_KEY_HEX:
        sys.exit("FATAL: SERVER_PUBLIC_KEY_HEX is not set. A device with no\n"
                 "       verification key cannot safely install firmware.\n"
                 "       Get it from: python tools/keygen.py (prints the hex)")
    try:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(SERVER_PUBLIC_KEY_HEX))
    except ValueError as exc:
        sys.exit(f"FATAL: SERVER_PUBLIC_KEY_HEX is not a valid key: {exc}")


def fail_update(client: mqtt.Client, campaign_id: str, reason: str,
                detail: str = "", chunk_index: int | None = None) -> None:
    global active
    log.error("UPDATE FAILED %s %s", reason, detail)
    client.publish(T_OTA_RESULT, envelope(
        schema="convoy.result.v1", campaign_id=campaign_id, success=False,
        reason_code=reason, detail=detail, chunk_index=chunk_index,
        battery=round(battery), network_quality=NETWORK_QUALITY,
    ), qos=1)
    active = None
    state["resume"] = None
    save_state(state)


def handle_offer(client: mqtt.Client, wire: dict) -> None:
    global active, injector

    try:
        manifest = ota.verify_offer(
            wire, server_public_key(), device_id=DEVICE_ID,
            min_allowed_version_code=state.get("min_allowed_version_code", 0))
    except ManifestRejected as exc:
        campaign_id = (wire.get("manifest") or {}).get("campaign_id", "unknown")
        log.error("offer REJECTED: %s", exc)
        client.publish(T_OTA_ACK, envelope(
            schema="convoy.ack.v1", campaign_id=campaign_id, accepted=False,
            reason_code=exc.reason, detail=exc.detail), qos=1)
        return

    campaign_id = manifest["campaign_id"]
    log.info("offer verified: v%s -> %d chunks, %d bytes",
             manifest["version"], manifest["chunk_count"], manifest["size"])

    injector = FailureInjector(FAILURE_MODE, FAILURE_PROBABILITY,
                               manifest["chunk_count"])

    # Local safety gate. The server already checked eligibility, but conditions
    # change between the check and the offer arriving, and the device is the
    # last authority on its own state.
    injected = injector.at_offer(round(battery), NETWORK_QUALITY, manifest)
    if injected:
        client.publish(T_OTA_ACK, envelope(
            schema="convoy.ack.v1", campaign_id=campaign_id, accepted=False,
            reason_code=injected,
            detail=f"battery {round(battery)}% below "
                   f"{manifest['min_battery']}% minimum"), qos=1)
        fail_update(client, campaign_id, injected,
                    f"battery {round(battery)}% < {manifest['min_battery']}%")
        return

    if round(battery) < manifest["min_battery"]:
        reason = ReasonCode.FAILED_LOW_BATTERY
        client.publish(T_OTA_ACK, envelope(
            schema="convoy.ack.v1", campaign_id=campaign_id, accepted=False,
            reason_code=reason), qos=1)
        fail_update(client, campaign_id, reason,
                    f"battery {round(battery)}% < {manifest['min_battery']}%")
        return

    active = Download(
        campaign_id=campaign_id, firmware_id=manifest["firmware_id"],
        version=manifest["version"], version_code=manifest["version_code"],
        chunk_count=manifest["chunk_count"], sha256=manifest["sha256"],
        chunk_hashes=manifest["chunk_hashes"], nonce=manifest["nonce"],
    )
    global is_rollback
    is_rollback = bool(manifest.get("rollback", False))
    if is_rollback:
        log.warning("this offer is a ROLLBACK to v%s (from v%s)",
                    manifest["version"], state["current_version"])
    client.publish(T_OTA_ACK, envelope(
        schema="convoy.ack.v1", campaign_id=campaign_id, accepted=True,
        nonce=manifest["nonce"]), qos=1)
    log.info("offer ACCEPTED, awaiting chunks")


def handle_chunk(client: mqtt.Client, payload: dict) -> None:
    global active

    if active is None or payload.get("campaign_id") != active.campaign_id:
        return  # stray chunk from a cancelled or previous campaign

    index, data, sha = ota.decode_chunk(payload)

    injected = injector.at_chunk(index) if injector else None
    if injected:
        fail_update(client, active.campaign_id, injected,
                    f"link failed at chunk {index}/{active.chunk_count}", index)
        return

    if injector:
        data = injector.corrupt(index, data)

    try:
        active.accept_chunk(index, data, sha)
    except ManifestRejected as exc:
        fail_update(client, active.campaign_id, exc.reason, exc.detail, index)
        return

    client.publish(T_OTA_PROGRESS, envelope(
        schema="convoy.progress.v1", campaign_id=active.campaign_id,
        chunk_index=index, chunk_count=active.chunk_count,
        percent=round(active.percent, 1)), qos=1)

    # Persist resume state periodically, not per chunk: a write every chunk
    # would dominate the transfer cost, and losing at most 8 chunks of progress
    # is an acceptable trade for a download measured in seconds.
    if index % 8 == 0 or active.complete:
        state["resume"] = {"campaign_id": active.campaign_id,
                           "firmware_id": active.firmware_id,
                           "next_chunk": active.next_index}
        save_state(state)

    if active.complete:
        install(client)


def install(client: mqtt.Client) -> None:
    """Verify the whole image, then swap slots.

    The simulated device does what a real one does in the same order: hash the
    complete image, mark the inactive slot bootable, record the new version,
    and only then report success. The A/B slot swap is symbolic here but the
    ORDER is what matters -- the previous version is never discarded until the
    new one is verified.
    """
    global active
    assert active is not None
    campaign_id = active.campaign_id

    try:
        image = active.assemble()
    except ManifestRejected as exc:
        fail_update(client, campaign_id, exc.reason, exc.detail)
        return

    injected = injector.at_install() if injector else None
    if injected:
        fail_update(client, campaign_id, injected, "flash write error")
        return

    previous_slot = state.get("active_slot", "A")
    previous_version = state["current_version"]
    previous_code = state.get("min_allowed_version_code", 0)
    new_slot = "B" if previous_slot == "A" else "A"

    # Write the new slot and point the bootloader at it. The PREVIOUS slot is
    # deliberately left intact -- that is the whole point of A/B, and it is
    # what makes the next few lines survivable.
    state["previous_version"] = previous_version
    state["previous_slot"] = previous_slot
    state["current_version"] = active.version
    state["min_allowed_version_code"] = active.version_code
    state["active_slot"] = new_slot
    state["resume"] = None
    save_state(state)

    log.info("INSTALLED v%s (%d bytes) slot %s -> %s",
             active.version, len(image), previous_slot, new_slot)

    # ---- self-confirmation -------------------------------------------------
    # A real device reboots here and the new image must announce itself within
    # a watchdog window. An image that writes and verifies perfectly can still
    # fail to run: the bytes are exactly what the server sent, they just do not
    # work on this hardware. Hashes and signatures cannot catch that. Only
    # keeping the old slot and requiring the new one to prove itself can.
    if injector and injector.breaks_boot():
        log.error("NEW IMAGE FAILED TO BOOT — reverting to v%s (slot %s)",
                  previous_version, previous_slot)
        state["current_version"] = previous_version
        state["active_slot"] = previous_slot
        state["min_allowed_version_code"] = previous_code
        state["previous_version"] = None
        save_state(state)

        client.publish(T_OTA_RESULT, envelope(
            schema="convoy.result.v1", campaign_id=campaign_id, success=False,
            reason_code=ReasonCode.ROLLED_BACK_AUTOMATIC,
            version=previous_version, active_slot=previous_slot,
            detail=f"v{active.version} did not confirm; reverted",
            battery=round(battery), network_quality=NETWORK_QUALITY), qos=1)
        active = None
        publish_hello(client, trigger="auto_rollback")
        return

    outcome = ReasonCode.ROLLED_BACK_MANUAL if is_rollback else ReasonCode.SUCCESS
    if is_rollback:
        log.warning("ROLLED BACK to v%s", active.version)

    client.publish(T_OTA_RESULT, envelope(
        schema="convoy.result.v1", campaign_id=campaign_id,
        success=not is_rollback, reason_code=outcome, version=active.version,
        active_slot=new_slot, battery=round(battery),
        network_quality=NETWORK_QUALITY), qos=1)

    active = None
    publish_hello(client, trigger="post_install")


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
            device_type=DEVICE_TYPE,
            model=DEVICE_MODEL,
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