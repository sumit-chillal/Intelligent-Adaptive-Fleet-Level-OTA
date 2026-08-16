"""
CONVOY — firmware publishing.

Takes a binary, turns it into a verifiable, resumable, signed artefact, and
records it immutably.

IMMUTABILITY
------------
Once a firmware row is PUBLISHED it is never edited. A new build is a new
version, always. This is not bureaucracy: campaign_targets rows reference a
firmware_id, and if the bytes behind that id could change, the audit trail
would be a lie -- "device X received firmware 1.4.0" would no longer identify
which bytes it actually received. The unique constraint on (model, version)
enforces it at the database level so no code path can bypass it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import FirmwareState
from app.core import crypto
from app.core.firmware import FirmwarePackage, package_firmware, version_to_code
from app.db.models import Firmware

log = structlog.get_logger(__name__)


class FirmwareError(Exception):
    pass


async def publish_firmware(
    session: AsyncSession,
    *,
    data: bytes,
    version: str,
    model: str = "tcu-sim-v1",
    notes: str | None = None,
    chunk_size: int | None = None,
) -> Firmware:
    """Package, hash, store and register a firmware image."""

    existing = await session.scalar(
        select(Firmware).where(Firmware.model == model, Firmware.version == version))
    if existing is not None:
        raise FirmwareError(
            f"firmware {model} {version} already exists (firmware_id="
            f"{existing.firmware_id}). Published firmware is immutable -- "
            f"bump the version instead of replacing it.")

    firmware_id = f"fw_{uuid.uuid4().hex[:12]}"
    pkg = package_firmware(
        data,
        firmware_id=firmware_id,
        version=version,
        model=model,
        chunk_size=chunk_size or settings.chunk_size_bytes,
    )

    storage_dir = Path(settings.firmware_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{firmware_id}_{model}_{version}.bin"
    storage_path.write_bytes(data)

    row = Firmware(
        firmware_id=pkg.firmware_id,
        version=pkg.version,
        version_code=pkg.version_code,
        model=pkg.model,
        size_bytes=pkg.size_bytes,
        sha256=pkg.sha256,
        chunk_size=pkg.chunk_size,
        chunk_count=pkg.chunk_count,
        chunk_hashes=list(pkg.chunk_hashes),
        state=str(FirmwareState.PUBLISHED),
        storage_path=str(storage_path),
        notes=notes,
    )
    session.add(row)
    await session.flush()

    log.info("firmware_published", firmware_id=firmware_id, version=version,
             model=model, size=pkg.size_bytes, chunks=pkg.chunk_count,
             sha256=pkg.sha256[:16])
    return row


async def load_package(session: AsyncSession, firmware_id: str) -> FirmwarePackage:
    """Re-chunk a stored image so the orchestrator can stream it.

    Chunks are recomputed from the file rather than stored in the database.
    Postgres is the wrong place for megabytes of binary that is already sitting
    on disk, and the integrity guarantee comes from the hashes in the signed
    manifest, not from where the bytes were kept.
    """
    row = await session.scalar(
        select(Firmware).where(Firmware.firmware_id == firmware_id))
    if row is None:
        raise FirmwareError(f"unknown firmware_id {firmware_id!r}")

    path = Path(row.storage_path)
    if not path.exists():
        raise FirmwareError(
            f"firmware file missing at {path}. The database row exists but the "
            f"image does not -- re-publish it.")

    data = path.read_bytes()
    pkg = package_firmware(data, firmware_id=row.firmware_id, version=row.version,
                           model=row.model, chunk_size=row.chunk_size)

    # If the file on disk no longer hashes to what was registered, something is
    # badly wrong. Refuse rather than distribute unknown bytes.
    if pkg.sha256 != row.sha256:
        raise FirmwareError(
            f"integrity failure: {path} hashes to {pkg.sha256[:16]} but the "
            f"database recorded {row.sha256[:16]}. Refusing to distribute.")
    return pkg


def load_signing_key():
    """Load the Ed25519 private key. Raises loudly if it is missing.

    Accepts either a path (local development) or the PEM itself in an env var
    (cloud deployment, where secrets are injected as values not files).
    """
    if settings.firmware_signing_private_key:
        return crypto.load_private_key(settings.firmware_signing_private_key.encode())
    if settings.firmware_signing_private_key_path:
        path = Path(settings.firmware_signing_private_key_path)
        if not path.exists():
            raise FirmwareError(
                f"signing key not found at {path}. Generate it with:\n"
                f"    python tools/keygen.py --out admin/secrets")
        return crypto.load_private_key(path)
    raise FirmwareError(
        "no signing key configured. Set FIRMWARE_SIGNING_PRIVATE_KEY_PATH in "
        "admin/.env")
