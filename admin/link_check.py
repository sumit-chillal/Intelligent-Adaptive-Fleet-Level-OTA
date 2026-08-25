#!/usr/bin/env python3
"""
CONVOY — ADMIN link checker  (runs on Laptop A / Mac only)

Purpose
-------
This is milestone zero. Before any database, dashboard, or rollout engine
exists, this script proves the single hardest requirement of the project:
devices on completely different networks can reach the server with no LAN,
no port forwarding, and no inbound firewall rule anywhere.

It connects to the broker as the SERVER identity, subscribes to the whole
device branch, and prints a live roster of every TCU that checks in --
whichever laptop, city, or hotspot it happens to be on.

Run it, then start containers on Laptop B. If rows appear here, the transport
layer is done and everything else is application code.

Usage
-----
    pip install paho-mqtt python-dotenv
    python link_check.py               # live roster
    python link_check.py --ping        # also broadcast a ping every 10s

Nothing here is hardcoded. Every credential comes from .env.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

HOST = os.environ["MQTT_HOST"]
PORT = int(os.getenv("MQTT_PORT", "8883"))
USER = os.environ["MQTT_USERNAME"]
PASSWORD = os.environ["MQTT_PASSWORD"]
ROOT = os.getenv("MQTT_TOPIC_ROOT", "convoy/v1")
TRANSPORT = os.getenv("MQTT_TRANSPORT", "tcp")
OFFLINE_TTL = int(os.getenv("DEVICE_OFFLINE_TTL_SECONDS", "20"))

DEVICE_WILDCARD = f"{ROOT}/d/+/#"
BROADCAST_CMD = f"{ROOT}/s/all/cmd"

roster: dict[str, dict] = {}
lock = threading.Lock()


def now() -> float:
    return time.time()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def on_connect(client, _userdata, _flags, rc, properties=None):
    if rc != 0:
        print(f"[{stamp()}] BROKER REFUSED CONNECTION rc={rc} — check credentials")
        return
    print(f"[{stamp()}] connected to {HOST}:{PORT} as {USER}")
    client.subscribe(DEVICE_WILDCARD, qos=1)
    print(f"[{stamp()}] subscribed to {DEVICE_WILDCARD}")

    # `hello` is a one-shot message published when a device connects. If this
    # server starts AFTER the devices did, those hellos are long gone and we
    # would never learn each device's firmware version or model -- the roster
    # would show '-' forever. So on every connect we broadcast an announce
    # request and the whole fleet re-introduces itself.
    client.publish(BROADCAST_CMD, json.dumps({
        "schema": "convoy.cmd.v1", "msg_id": str(uuid.uuid4()),
        "cmd": "announce", "jitter_s": 2, "sent_at": now(),
    }), qos=1)
    print(f"[{stamp()}] broadcast announce → {BROADCAST_CMD}")
    print(f"[{stamp()}] waiting for devices...\n")


def on_message(_client, _userdata, msg: mqtt.MQTTMessage):
    parts = msg.topic.split("/")
    # convoy/v1/d/{device_id}/{leaf...}
    if len(parts) < 5:
        return
    device_id = parts[3]
    leaf = "/".join(parts[4:])

    # An EMPTY retained payload is MQTT's deletion marker — it is how --forget
    # erases a device's retained status. Treating it as a message would parse
    # to nothing and then default `online` to True, so clearing a ghost device
    # made it immediately reappear as ONLINE, which is the opposite of the
    # intended effect.
    if not msg.payload:
        return

    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"_raw": len(msg.payload)}

    with lock:
        entry = roster.setdefault(
            device_id,
            {"first_seen": now(), "battery": None, "network": None,
             "version": None, "type": None, "online": True, "msgs": 0},
        )
        entry["last_seen"] = now()
        entry["msgs"] += 1

        if leaf == "hello":
            entry["version"] = payload.get("current_version")
            entry["type"] = payload.get("device_type")
            entry["online"] = True
            print(f"[{stamp()}] HELLO    {device_id:<14} "
                  f"v{payload.get('current_version')} "
                  f"model={payload.get('model')} "
                  f"ip_hint={payload.get('net_hint', '-')}")
        elif leaf == "health":
            entry["battery"] = payload.get("battery")
            entry["network"] = payload.get("network_quality")
            entry["online"] = True
            # Health carries identity too, so the roster recovers even if the
            # announce round trip was lost.
            entry["version"] = payload.get("current_version") or entry["version"]
            entry["type"] = payload.get("device_type") or entry["type"]
        elif leaf == "status":
            entry["online"] = bool(payload.get("online", True))
            state = "ONLINE" if entry["online"] else "OFFLINE (last will fired)"
            print(f"[{stamp()}] STATUS   {device_id:<14} {state}")
        elif leaf == "pong":
            # Both timestamps come from THIS machine's clock (we stamp sent_at
            # and the device echoes it back untouched), so clock skew between
            # laptops cannot distort the measurement. This is a true round trip:
            # server -> broker -> device -> broker -> server, i.e. four WAN legs.
            rtt = (now() - payload.get("sent_at", now())) * 1000
            entry.setdefault("rtts", []).append(rtt)
            entry["rtts"] = entry["rtts"][-20:]
            print(f"[{stamp()}] PONG     {device_id:<14} rtt={rtt:6.1f} ms")


def print_roster():
    while True:
        time.sleep(5)
        with lock:
            if not roster:
                continue
            print(f"\n  ── FLEET ROSTER {stamp()} " + "─" * 42)
            print(f"  {'DEVICE':<16}{'TYPE':<10}{'VER':<9}{'BATT':>6}{'NET':>5}"
                  f"{'RTT':>9}{'MSGS':>7}  STATE")
            online = 0
            for did in sorted(roster):
                e = roster[did]
                stale = (now() - e["last_seen"]) > OFFLINE_TTL
                state = "OFFLINE" if (stale or not e["online"]) else "online"
                online += state == "online"
                rtts = e.get("rtts") or []
                rtt = f"{statistics.median(rtts):.0f}ms" if rtts else "-"
                print(f"  {did:<16}{str(e['type'] or '-'):<10}"
                      f"{str(e['version'] or '-'):<9}"
                      f"{str(e['battery'] or '-'):>5}%{str(e['network'] or '-'):>5}"
                      f"{rtt:>9}{e['msgs']:>7}  {state}")
            print(f"  online={online}/{len(roster)}  " + "─" * 46 + "\n")


def ping_loop(client: mqtt.Client):
    while True:
        time.sleep(10)
        with lock:
            targets = list(roster)
        for did in targets:
            client.publish(
                f"{ROOT}/s/{did}/cmd",
                json.dumps({"schema": "convoy.cmd.v1", "msg_id": str(uuid.uuid4()),
                            "cmd": "ping", "sent_at": now()}),
                qos=1,
            )


def forget(client: mqtt.Client, device_ids: list[str]) -> None:
    """Erase a device's retained status message from the broker.

    A retained message lives on the broker forever until it is explicitly
    cleared, so a decommissioned or test device keeps showing up as a ghost row
    on every future connect. Publishing an EMPTY payload with retain=True is the
    MQTT-defined way to delete a retained message.
    """
    for did in device_ids:
        topic = f"{ROOT}/d/{did}/status"
        client.publish(topic, payload=b"", qos=1, retain=True).wait_for_publish(5)
        print(f"[{stamp()}] cleared retained status for {did}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="broadcast ping every 10s")
    ap.add_argument("--forget", nargs="+", metavar="DEVICE_ID",
                    help="clear a retired device's retained status, then exit")
    args = ap.parse_args()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"convoy-linkcheck-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv5,
        transport="websockets" if TRANSPORT == "websockets" else "tcp",
    )
    client.username_pw_set(USER, PASSWORD)
    # TLS with real certificate verification. Never disable this (Rules.md §6).
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.on_connect = on_connect
    client.on_message = on_message

    if args.forget:
        client.connect(HOST, PORT, keepalive=30)
        client.loop_start()
        time.sleep(1)
        forget(client, args.forget)
        time.sleep(1)
        client.loop_stop()
        client.disconnect()
        return 0

    threading.Thread(target=print_roster, daemon=True).start()

    print(f"[{stamp()}] dialling {HOST}:{PORT} ...")
    client.connect(HOST, PORT, keepalive=30)

    if args.ping:
        threading.Thread(target=ping_loop, args=(client,), daemon=True).start()

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())