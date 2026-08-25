"""
CONVOY — device-side OTA.

Everything a TCU does with a firmware offer: verify it came from the authorised
server, decide whether it is safe to install right now, download it in
hash-checked chunks that survive an interruption, and swap slots.

RELATIONSHIP TO THE SERVER'S crypto.py
--------------------------------------
This deliberately re-implements manifest verification rather than importing the
server's module. The device tree and the server tree share no code by design
(Rules.md R7) -- the ESP32 firmware is C and cannot import Python either, so
the protocol has to be independently implementable from its specification. What
must never diverge is the canonical byte encoding: sorted keys, no whitespace,
UTF-8. If that drifts, valid manifests start failing verification and somebody
"fixes" it by turning verification off.

WHY VERIFY BEFORE ALLOCATING
----------------------------
The signature is checked before a single byte of buffer is reserved. Reading
manifest["chunk_count"] to size an array before knowing the manifest is genuine
would mean sizing an allocation from attacker-controlled data.

FAILURE SIMULATION
------------------
FAILURE_MODE turns a healthy device into one that fails in a specific,
reproducible way. This is what makes tcu_D_004 and tcu_D_005 fail ON CAMERA and
drive the adaptive engine, rather than being quietly skipped by the server's
eligibility gate before the update ever starts.
"""

from __future__ import annotations

import base64
import hashlib
import os
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class ReasonCode:
    """Mirror of the server's closed vocabulary. Keep in step by hand."""

    SUCCESS = "SUCCESS"
    FAILED_LOW_BATTERY = "FAILED_LOW_BATTERY"
    FAILED_POOR_NETWORK = "FAILED_POOR_NETWORK"
    FAILED_TIMEOUT = "FAILED_TIMEOUT"
    FAILED_CHUNK_HASH_MISMATCH = "FAILED_CHUNK_HASH_MISMATCH"
    FAILED_IMAGE_HASH_MISMATCH = "FAILED_IMAGE_HASH_MISMATCH"
    FAILED_SIGNATURE_INVALID = "FAILED_SIGNATURE_INVALID"
    FAILED_ANTI_ROLLBACK = "FAILED_ANTI_ROLLBACK"
    FAILED_FLASH_WRITE = "FAILED_FLASH_WRITE"
    ROLLED_BACK_MANUAL = "ROLLED_BACK_MANUAL"
    ROLLED_BACK_AUTOMATIC = "ROLLED_BACK_AUTOMATIC"


class ManifestRejected(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def canonical_bytes(manifest: dict) -> bytes:
    """MUST match the server byte for byte."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def verify_offer(wire: dict, public_key: Ed25519PublicKey, *, device_id: str,
                 min_allowed_version_code: int = 0) -> dict:
    """Check provenance FIRST, then the fields. Raises ManifestRejected."""
    manifest = wire.get("manifest")
    signature_hex = wire.get("signature")
    if not isinstance(manifest, dict) or not isinstance(signature_hex, str):
        raise ManifestRejected(ReasonCode.FAILED_SIGNATURE_INVALID,
                               "malformed offer envelope")
    if wire.get("sig_alg") != "ed25519":
        raise ManifestRejected(ReasonCode.FAILED_SIGNATURE_INVALID,
                               f"unsupported algorithm {wire.get('sig_alg')!r}")
    try:
        public_key.verify(bytes.fromhex(signature_hex), canonical_bytes(manifest))
    except (InvalidSignature, ValueError) as exc:
        raise ManifestRejected(ReasonCode.FAILED_SIGNATURE_INVALID,
                               str(exc) or "bad signature")

    # Only now is it safe to trust any field.
    if manifest.get("device_id") != device_id:
        raise ManifestRejected(
            ReasonCode.FAILED_SIGNATURE_INVALID,
            f"addressed to {manifest.get('device_id')!r}, not us")

    version_code = int(manifest.get("version_code", -1))
    if version_code < min_allowed_version_code and not manifest.get("rollback", False):
        raise ManifestRejected(
            ReasonCode.FAILED_ANTI_ROLLBACK,
            f"version_code {version_code} below floor {min_allowed_version_code}")
    return manifest


@dataclass
class Download:
    """One in-flight update, backed by a FILE rather than by memory.

    WHY A FILE
    ----------
    The first version accumulated chunks in a dict. Resume then worked on
    paper and failed in practice: the device could ask the server to continue
    from chunk 9, but a restarted process had lost chunks 0-8 AND the manifest,
    so every arriving chunk hit `active is None` and was dropped. It resumed
    forever without progressing.

    A real ECU writes each verified chunk straight into the inactive flash
    slot; that partially-written slot is what survives a power cut and makes
    resume mean something. Writing to a file on the mounted volume models the
    same thing, and it makes the resume point self-evident: the number of bytes
    already on disk IS the progress. No separate counter to drift.
    """

    campaign_id: str
    firmware_id: str
    version: str
    version_code: int
    chunk_count: int
    chunk_size: int
    sha256: str
    chunk_hashes: list[str]
    nonce: str
    path: Path
    manifest: dict
    is_rollback: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def next_index(self) -> int:
        """Derived from the file, never tracked separately.

        A counter kept alongside the data can disagree with it after a crash
        between the write and the counter update. The file length cannot.
        """
        if not self.path.exists():
            return 0
        return self.path.stat().st_size // self.chunk_size

    @property
    def complete(self) -> bool:
        return self.next_index >= self.chunk_count

    @property
    def percent(self) -> float:
        return 100.0 * self.next_index / self.chunk_count if self.chunk_count else 0.0

    def accept_chunk(self, index: int, data: bytes) -> bool:
        """Verify and append. Returns False for a chunk we already have.

        Chunks are appended strictly in order. The server streams sequentially,
        so an out-of-order arrival means a duplicate from a QoS 1 redelivery or
        an overlapping stream after a resume -- both are safe to drop, and
        dropping them is what keeps the file a faithful prefix of the image.
        """
        expected_index = self.next_index
        if index < expected_index:
            return False           # already written
        if index > expected_index:
            return False           # gap: wait for the one we need

        expected_hash = self.chunk_hashes[index]
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_hash:
            raise ManifestRejected(
                ReasonCode.FAILED_CHUNK_HASH_MISMATCH,
                f"chunk {index}: expected {expected_hash[:12]}, got {actual[:12]}")

        with self.path.open("ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())   # survive a power cut, like a flash write
        return True

    def assemble(self) -> bytes:
        """Read the completed slot back and verify the whole image."""
        if not self.complete:
            raise ManifestRejected(
                ReasonCode.FAILED_IMAGE_HASH_MISMATCH,
                f"incomplete: {self.next_index}/{self.chunk_count} chunks")
        image = self.path.read_bytes()
        actual = hashlib.sha256(image).hexdigest()
        if actual != self.sha256:
            raise ManifestRejected(
                ReasonCode.FAILED_IMAGE_HASH_MISMATCH,
                f"image hash {actual[:12]} != manifest {self.sha256[:12]}")
        return image

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)

    def to_state(self) -> dict:
        """What must survive a restart: where we are, and what we agreed to.

        The MANIFEST is stored, not just the chunk index. Without it a
        restarted device knows how far it got but not what it was downloading,
        so it cannot verify or accept another chunk.
        """
        return {"campaign_id": self.campaign_id,
                "firmware_id": self.firmware_id,
                "path": str(self.path),
                "manifest": self.manifest,
                "is_rollback": self.is_rollback}

    @classmethod
    def from_manifest(cls, manifest: dict, state_dir: Path,
                      is_rollback: bool = False) -> "Download":
        path = state_dir / f"{manifest['campaign_id']}_{manifest['firmware_id']}.part"
        return cls(
            campaign_id=manifest["campaign_id"],
            firmware_id=manifest["firmware_id"],
            version=manifest["version"],
            version_code=manifest["version_code"],
            chunk_count=manifest["chunk_count"],
            chunk_size=manifest["chunk_size"],
            sha256=manifest["sha256"],
            chunk_hashes=manifest["chunk_hashes"],
            nonce=manifest["nonce"],
            path=path,
            manifest=manifest,
            is_rollback=is_rollback,
        )


class FailureInjector:
    """Turns a configured FAILURE_MODE into a specific, reproducible failure.

    Kept in one class so the failure behaviour is auditable in one place and
    cannot leak into the real code paths. A device with mode 'none' runs
    through here unaffected.
    """

    def __init__(self, mode: str, probability: float, chunk_count_hint: int = 32) -> None:
        self.mode = (mode or "none").lower()
        self.probability = probability
        self._fail_at = None
        if self.mode in ("drop_midway", "poor_network"):
            self._fail_at = random.randint(max(1, chunk_count_hint // 4),
                                           max(2, chunk_count_hint * 3 // 4))

    @property
    def active(self) -> bool:
        return self.mode != "none" and random.random() < max(self.probability, 0.0)

    def at_offer(self, battery: int, network: int, manifest: dict) -> str | None:
        """Fail immediately on receiving the offer."""
        if self.mode == "low_battery" and self.active:
            return ReasonCode.FAILED_LOW_BATTERY
        if self.mode == "checksum" and self.active:
            return None  # surfaces later, at chunk verification
        return None

    def at_chunk(self, index: int) -> str | None:
        """Fail part-way through the download."""
        if self._fail_at is None or index < self._fail_at:
            return None
        if self.mode == "poor_network":
            return ReasonCode.FAILED_POOR_NETWORK
        if self.mode == "drop_midway":
            return ReasonCode.FAILED_TIMEOUT
        return None

    def corrupt(self, index: int, data: bytes) -> bytes:
        """Flip a byte so the chunk hash check fires — proving it works."""
        if self.mode == "checksum" and index == 3 and self.active:
            return bytes([data[0] ^ 0xFF]) + data[1:]
        return data

    def at_install(self) -> str | None:
        if self.mode == "flash_error" and self.active:
            return ReasonCode.FAILED_FLASH_WRITE
        return None

    def breaks_boot(self) -> bool:
        """Whether the newly installed image will fail to self-confirm.

        This models the failure that A/B partitioning exists for: an image that
        writes and verifies perfectly, then does not come back after the
        reboot. Hash checks cannot catch it -- the bytes are exactly what the
        server sent, they just do not work on this device. The only defence is
        keeping the previous slot intact until the new one proves itself.
        """
        return self.mode == "bad_boot" and self.active


def decode_chunk(payload: dict) -> tuple[int, bytes, str]:
    index = int(payload["index"])
    data = base64.b64decode(payload["data"])
    return index, data, payload.get("sha256", "")