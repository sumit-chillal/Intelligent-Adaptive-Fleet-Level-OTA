#!/usr/bin/env python3
"""
CONVOY — admin CLI.

Everything the dashboard will eventually do, available from a terminal first.
Building the CLI before the UI means the campaign logic is exercised and
debugged without a browser in the way, and it gives you a fallback if anything
goes wrong with the dashboard on demo day.

Run from convoy/backend/ with the venv active:

    python manage.py devices
    python manage.py firmware:make --version 1.4.0 --size 256
    python manage.py firmware:publish --file build/tcu-1.4.0.bin --version 1.4.0
    python manage.py firmware:list
    python manage.py campaign:dryrun --firmware fw_abc123
    python manage.py campaign:create --firmware fw_abc123 --name "fleet 1.4.0" \\
        --batch-size 5 --abort-threshold 0.5
    python manage.py campaign:list
    python manage.py campaign:show --campaign c_abc123
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.core.crypto import sign_manifest
from app.core.firmware import build_manifest
from app.db.models import (
    Campaign,
    CampaignTarget,
    Device,
    DeviceEvent,
    DeviceHealthSample,
    Firmware,
)
from app.db.session import check_connection, dispose_engine, session_scope
from app.services import campaign_service as cs
from app.services import firmware_service as fs

G, R, Y, B, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m"


# ------------------------------------------------------------------ devices --
async def cmd_devices(_args) -> int:
    async with session_scope() as session:
        rows = list(await session.scalars(select(Device).order_by(Device.device_id)))
    if not rows:
        print("No devices registered yet. Start the bridge and some containers.")
        return 0
    print(f"\n  {'DEVICE':<16}{'FLEET':<10}{'MODEL':<14}{'VER':<9}"
          f"{'BATT':>6}{'NET':>5}  STATE")
    for d in rows:
        state = f"{G}online{RESET}" if d.online else f"{Y}OFFLINE{RESET}"
        print(f"  {d.device_id:<16}{str(d.fleet_tag or '-'):<10}{d.model:<14}"
              f"{str(d.current_version or '-'):<9}"
              f"{str(d.battery if d.battery is not None else '-'):>5}%"
              f"{str(d.network_quality or '-'):>5}  {state}")
    print(f"  {sum(1 for d in rows if d.online)}/{len(rows)} online\n")
    return 0


async def cmd_device_remove(args) -> int:
    """Delete a device and its history. For test artefacts only.

    Refuses if the device took part in any campaign: campaign_targets and
    device_events reference it, and deleting it would leave a rollout whose
    per-device outcomes no longer add up. History that can be quietly deleted
    is not an audit trail (Rules.md R5).
    """
    async with session_scope() as session:
        device = await session.scalar(
            select(Device).where(Device.device_id == args.device))
        if device is None:
            print(f"{R}unknown device {args.device!r}{RESET}")
            return 1

        used = await session.scalar(
            select(CampaignTarget).where(CampaignTarget.device_id == args.device))
        if used is not None and not args.force:
            print(f"{R}refusing:{RESET} {args.device} took part in campaign "
                  f"{used.campaign_id}. Its history is referenced by that "
                  f"rollout's results.")
            print(f"  Pass --force to delete anyway (the campaign's per-device "
                  f"outcomes will no longer add up).")
            return 1

        if args.force:
            await session.execute(
                delete(CampaignTarget).where(CampaignTarget.device_id == args.device))
            await session.execute(
                delete(DeviceEvent).where(DeviceEvent.device_id == args.device))
            await session.execute(
                delete(DeviceHealthSample).where(
                    DeviceHealthSample.device_id == args.device))

        await session.delete(device)
        print(f"{Y}removed{RESET} {args.device}")
        print(f"  Also clear its retained broker status, or it reappears:")
        print(f"      cd ../admin && python link_check.py --forget {args.device}")
    return 0


# ----------------------------------------------------------------- firmware --
async def cmd_firmware_make(args) -> int:
    """Generate a dummy firmware image for testing.

    Deterministic content so the same version always produces the same hash,
    which makes it obvious in the logs when you accidentally rebuild.
    """
    size = args.size * 1024
    seed = f"CONVOY-FIRMWARE-v{args.version}-".encode()
    data = (seed * (size // len(seed) + 1))[:size]

    out = Path(args.out or f"build/tcu-{args.version}.bin")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{G}wrote{RESET} {out}  ({len(data):,} bytes)")
    print(f"  next: python manage.py firmware:publish --file {out} "
          f"--version {args.version}")
    return 0


async def cmd_firmware_publish(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"{R}no such file:{RESET} {path}")
        return 1

    # Load the signing key FIRST, before anything is written. Publishing a
    # firmware row and then discovering the key is missing leaves an
    # unsignable image registered in the database -- and because published
    # firmware is immutable, the only way out is deleting the row by hand.
    # Fail before the write, not after it.
    try:
        key = fs.load_signing_key()
    except fs.FirmwareError as exc:
        print(f"{R}{exc}{RESET}")
        return 1

    async with session_scope() as session:
        try:
            row = await fs.publish_firmware(
                session, data=path.read_bytes(), version=args.version,
                model=args.model, notes=args.notes)

            # Sign a sample manifest now. If signing is broken you want to
            # know at publish time, not when the first device rejects an offer
            # mid-demo.
            pkg = await fs.load_package(session, row.firmware_id)
            manifest = build_manifest(pkg, device_id="_selftest",
                                      campaign_id="_selftest", min_battery=30,
                                      min_network_quality=2)
            signed = sign_manifest(manifest, key)
        except fs.FirmwareError as exc:
            print(f"{R}{exc}{RESET}")
            raise SystemExit(1)

        print(f"\n  {G}published{RESET}")
        print(f"  firmware_id  {row.firmware_id}")
        print(f"  version      {row.version}  (code {row.version_code})")
        print(f"  model        {row.model}")
        print(f"  size         {row.size_bytes:,} bytes")
        print(f"  sha256       {row.sha256}")
        print(f"  chunks       {row.chunk_count} x {row.chunk_size} bytes")
        print(f"  signature    {G}signed ok{RESET} ({len(signed.signature)} bytes)")
        print(f"\n  next: python manage.py campaign:dryrun "
              f"--firmware {row.firmware_id}\n")
    return 0


async def cmd_firmware_delete(args) -> int:
    """Remove a firmware row. Only for cleaning up mistakes during development.

    Published firmware is immutable by design, so this refuses to touch
    anything a campaign has referenced -- deleting that would orphan the audit
    trail and break the guarantee that a firmware_id identifies specific bytes.
    """
    async with session_scope() as session:
        row = await session.scalar(
            select(Firmware).where(Firmware.firmware_id == args.firmware))
        if row is None:
            print(f"{R}unknown firmware_id {args.firmware!r}{RESET}")
            return 1
        used = await session.scalar(
            select(Campaign).where(Campaign.firmware_id == args.firmware))
        if used is not None:
            print(f"{R}refusing:{RESET} campaign {used.campaign_id} references "
                  f"this firmware. History must stay intact.")
            return 1
        await session.delete(row)
        print(f"{Y}deleted{RESET} {args.firmware} ({row.model} {row.version})")
    return 0


async def cmd_firmware_list(_args) -> int:
    async with session_scope() as session:
        rows = list(await session.scalars(
            select(Firmware).order_by(Firmware.created_at.desc())))
    if not rows:
        print("No firmware published yet.")
        return 0
    print(f"\n  {'FIRMWARE_ID':<18}{'VERSION':<10}{'MODEL':<14}"
          f"{'SIZE':>10}{'CHUNKS':>8}  {'SHA256':<18}STATE")
    for f in rows:
        print(f"  {f.firmware_id:<18}{f.version:<10}{f.model:<14}"
              f"{f.size_bytes:>10,}{f.chunk_count:>8}  {f.sha256[:16]:<18}{f.state}")
    print()
    return 0


# ---------------------------------------------------------------- campaigns --
async def cmd_campaign_dryrun(args) -> int:
    async with session_scope() as session:
        try:
            entries = await cs.dry_run(
                session, firmware_id=args.firmware,
                selector=cs.Selector(fleet_tags=args.fleet, device_ids=args.device),
                min_battery=args.min_battery,
                min_network_quality=args.min_network)
        except cs.CampaignError as exc:
            print(f"{R}{exc}{RESET}")
            return 1

    eligible = [e for e in entries if e.eligible]
    skipped = [e for e in entries if not e.eligible]

    print(f"\n  {B}DRY RUN{RESET} — nothing has been created.\n")
    print(f"  {G}WOULD UPDATE ({len(eligible)}){RESET}")
    for e in eligible:
        print(f"    {e.device_id:<16} battery {e.battery}%  network {e.network_quality}")
    if skipped:
        print(f"\n  {Y}WOULD SKIP ({len(skipped)}){RESET}")
        for e in skipped:
            print(f"    {e.device_id:<16} {e.reason}")
            print(f"    {'':<16} {e.detail}")
    print(f"\n  {len(eligible)} of {len(entries)} devices ready.\n")
    return 0


async def cmd_campaign_create(args) -> int:
    async with session_scope() as session:
        try:
            campaign, count = await cs.create_campaign(
                session,
                name=args.name,
                firmware_id=args.firmware,
                selector=cs.Selector(fleet_tags=args.fleet, device_ids=args.device),
                batch_size=args.batch_size,
                canary_size=args.canary,
                min_battery=args.min_battery,
                min_network_quality=args.min_network,
                abort_threshold=args.abort_threshold,
                max_attempts=args.max_attempts,
            )
        except cs.CampaignError as exc:
            print(f"{R}{exc}{RESET}")
            return 1

        print(f"\n  {G}campaign created{RESET}")
        print(f"  campaign_id      {campaign.campaign_id}")
        print(f"  name             {campaign.name}")
        print(f"  targets          {count} devices (PENDING)")
        print(f"  batch size       {campaign.batch_size_initial} "
              f"(min {campaign.batch_size_min}, max {campaign.batch_size_max})")
        print(f"  canary           {campaign.canary_size}")
        print(f"  eligibility      battery >= {campaign.min_battery}%, "
              f"network >= {campaign.min_network_quality}")
        print(f"  shrink / abort   {campaign.shrink_threshold:.0%} / "
              f"{campaign.abort_threshold:.0%}")
        print(f"  max attempts     {campaign.max_attempts}"
              f"{'  (no retries)' if campaign.max_attempts == 1 else ''}")
        print(f"\n  state is DRAFT. The orchestrator will start it.\n")
    return 0


async def cmd_campaign_start(args) -> int:
    """Flip a campaign to RUNNING. The orchestrator picks it up on its next tick.

    Deliberately just a state change: starting a rollout from the CLI and
    having it run inside the bridge process means there is exactly one
    orchestrator, which is the invariant that stops two schedulers
    double-offering to the same device.
    """
    async with session_scope() as session:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.campaign_id == args.campaign))
        if campaign is None:
            print(f"{R}unknown campaign {args.campaign!r}{RESET}")
            return 1
        if campaign.state not in ("DRAFT", "PAUSED"):
            print(f"{R}campaign is {campaign.state}{RESET} — only DRAFT or "
                  f"PAUSED campaigns can be started.")
            return 1
        campaign.state = "RUNNING"
        campaign.started_at = campaign.started_at or datetime.now(timezone.utc)
        print(f"\n  {G}campaign {campaign.campaign_id} is now RUNNING{RESET}")
        print(f"  Watch the bridge terminal — the orchestrator opens the "
              f"canary batch within a couple of seconds.\n")
    return 0


async def cmd_campaign_pause(args) -> int:
    async with session_scope() as session:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.campaign_id == args.campaign))
        if campaign is None:
            print(f"{R}unknown campaign{RESET}")
            return 1
        campaign.state = "PAUSED"
        print(f"{Y}paused{RESET} {campaign.campaign_id}")
    return 0


async def cmd_campaign_rollback(args) -> int:
    """Create a rollback campaign returning a bad release to a known build."""
    from app.core.orchestrator import Orchestrator

    orch = Orchestrator(bridge=None)  # no publishing; creation only
    try:
        new_id = await orch.rollback_campaign(
            args.campaign, args.to_firmware, name=args.name,
            batch_size=args.batch_size)
    except ValueError as exc:
        print(f"{R}{exc}{RESET}")
        return 1

    async with session_scope() as session:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.campaign_id == new_id))
        firmware = await session.scalar(
            select(Firmware).where(Firmware.firmware_id == args.to_firmware))
        count = len(list(await session.scalars(
            select(CampaignTarget.device_id).where(
                CampaignTarget.campaign_id == new_id))))

    print(f"\n  {Y}ROLLBACK campaign created{RESET}")
    print(f"  campaign_id      {new_id}")
    print(f"  rolling back     {args.campaign}")
    print(f"  to version       {firmware.version}")
    print(f"  devices          {count} (only those the original UPDATED)")
    print(f"  batch size       {campaign.batch_size_initial}  (no canary — "
          f"the target is a build these devices already ran)")
    print(f"\n  state is DRAFT. Start it when you are ready:")
    print(f"      python manage.py campaign:start --campaign {new_id}\n")
    return 0


async def cmd_campaign_list(_args) -> int:
    async with session_scope() as session:
        rows = list(await session.scalars(
            select(Campaign).order_by(Campaign.created_at.desc())))
        if not rows:
            print("No campaigns yet.")
            return 0
        print(f"\n  {'CAMPAIGN_ID':<16}{'NAME':<24}{'STATE':<12}{'BATCH':>6}  PROGRESS")
        for c in rows:
            p = await cs.campaign_progress(session, c.campaign_id)
            print(f"  {c.campaign_id:<16}{c.name[:22]:<24}{c.state:<12}"
                  f"{c.current_batch_size:>6}  "
                  f"{p['succeeded']} ok / {p['failed']} failed / "
                  f"{p['skipped']} skipped / {p['pending']} pending")
        print()
    return 0


async def cmd_campaign_show(args) -> int:
    async with session_scope() as session:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.campaign_id == args.campaign))
        if campaign is None:
            print(f"{R}unknown campaign {args.campaign!r}{RESET}")
            return 1
        progress = await cs.campaign_progress(session, args.campaign)

    print(f"\n  {campaign.name}  ({campaign.campaign_id})")
    print(f"  state            {campaign.state}")
    print(f"  firmware         {campaign.firmware_id}")
    print(f"  batch size       {campaign.current_batch_size} "
          f"(started at {campaign.batch_size_initial})")
    print(f"  batches done     {campaign.batches_completed}")
    print(f"  ewma failure     {campaign.ewma_failure_rate:.3f}")
    print(f"  targets          {progress['total']}")
    for state, n in sorted(progress["by_state"].items()):
        print(f"    {state:<14} {n}")
    print()
    return 0


# --------------------------------------------------------------------- main --
COMMANDS = {
    "devices": cmd_devices,
    "device:remove": cmd_device_remove,
    "firmware:make": cmd_firmware_make,
    "firmware:publish": cmd_firmware_publish,
    "firmware:list": cmd_firmware_list,
    "firmware:delete": cmd_firmware_delete,
    "campaign:dryrun": cmd_campaign_dryrun,
    "campaign:create": cmd_campaign_create,
    "campaign:rollback": cmd_campaign_rollback,
    "campaign:start": cmd_campaign_start,
    "campaign:pause": cmd_campaign_pause,
    "campaign:list": cmd_campaign_list,
    "campaign:show": cmd_campaign_show,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CONVOY admin CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="list registered devices")

    rm = sub.add_parser("device:remove",
                        help="delete a test device and its history")
    rm.add_argument("--device", required=True)
    rm.add_argument("--force", action="store_true",
                    help="delete even if it appears in campaign history")

    m = sub.add_parser("firmware:make", help="generate a test firmware image")
    m.add_argument("--version", required=True)
    m.add_argument("--size", type=int, default=256, help="size in KiB")
    m.add_argument("--out")

    pub = sub.add_parser("firmware:publish", help="package, sign and register")
    pub.add_argument("--file", required=True)
    pub.add_argument("--version", required=True)
    pub.add_argument("--model", default="tcu-sim-v1")
    pub.add_argument("--notes")

    sub.add_parser("firmware:list", help="list published firmware")

    dele = sub.add_parser("firmware:delete",
                          help="delete an unused firmware row (dev cleanup only)")
    dele.add_argument("--firmware", required=True)

    for name, help_text in [("campaign:dryrun", "preview without creating"),
                            ("campaign:create", "create a campaign")]:
        c = sub.add_parser(name, help=help_text)
        c.add_argument("--firmware", required=True)
        c.add_argument("--fleet", nargs="+", help="fleet tags, e.g. laptopB laptopD")
        c.add_argument("--device", nargs="+", help="explicit device ids")
        c.add_argument("--min-battery", type=int, default=30)
        c.add_argument("--min-network", type=int, default=2)
        if name == "campaign:create":
            c.add_argument("--name", required=True)
            c.add_argument("--batch-size", type=int)
            c.add_argument("--canary", type=int)
            # Memory.md D15: the demo needs 0.50, not the 0.40 default.
            c.add_argument("--abort-threshold", type=float,
                           help="failure rate that aborts the campaign (demo: 0.5)")
            c.add_argument("--max-attempts", type=int,
                           help="attempts per device before giving up. "
                                "1 = no retries, which keeps a permanently "
                                "broken device from filling a later batch and "
                                "tripping the abort guard (demo: 1)")

    for nm, hlp in [("campaign:start", "set a campaign RUNNING"),
                    ("campaign:pause", "hold a running campaign")]:
        c = sub.add_parser(nm, help=hlp)
        c.add_argument("--campaign", required=True)

    rb = sub.add_parser("campaign:rollback",
                        help="roll a campaign back to a known-good version")
    rb.add_argument("--campaign", required=True, help="the campaign to undo")
    rb.add_argument("--to-firmware", required=True,
                    help="firmware_id of the known-good build")
    rb.add_argument("--name")
    rb.add_argument("--batch-size", type=int)

    sub.add_parser("campaign:list", help="list campaigns")
    show = sub.add_parser("campaign:show", help="campaign detail")
    show.add_argument("--campaign", required=True)
    return p


async def run() -> int:
    args = build_parser().parse_args()
    ok, detail = await check_connection()
    if not ok:
        print(f"{R}Cannot reach Postgres.{RESET} {detail}")
        print("  cd ../admin && docker compose -f docker-compose.admin.yml "
              "--env-file .env up -d")
        return 1
    try:
        return await COMMANDS[args.command](args)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(130)