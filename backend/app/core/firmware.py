"""
CONVOY — firmware packaging: turning a .bin into a verifiable, resumable stream.

Why chunks and not a download URL
---------------------------------
The obvious design is "put the image on HTTPS and send the device a link". It
is rejected here for three reasons that all come from the project's own
constraints:

  1. No inbound ports. Requirement 18 says devices may be on a hotspot, behind
     CGNAT, in another city. A server that must be reachable at a public URL
     needs hosting, DNS, and a certificate; a server that only publishes to a
     broker needs none of those. Everything stays outbound-only.
  2. Resume falls out for free. Requirement 11 wants an interrupted update to
     continue where it stopped. With a numbered, individually-hashed chunk
     stream, "where it stopped" is just an integer the device persists.
  3. One transport to secure, monitor, and demo. Progress, failure, retry, and
     firmware data all flow over the same audited channel.

The manifest keeps a `download_url` field reserved so that images too large for
a comfortable MQTT stream can switch to HTTPS later without a protocol change.
The integrity story is unchanged either way, because the hashes live in the
signed manifest, not in the transport.

Chunk size
----------
8 KiB raw. Base64 inflates by 4/3, so the wire payload is ~11 KiB, comfortably
inside broker payload limits with room for the JSON envelope. Small enough that
an ESP32 can hold one in RAM and write it straight to flash without buffering
the whole image.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from app.core.crypto import MANIFEST_SCHEMA, new_nonce, sha256_hex

DEFAULT_CHUNK_SIZE = 8192


@dataclass(frozen=True)
class FirmwarePackage:
    """The immutable result of ingesting one firmware binary."""

    firmware_id: str
    version: str
    version_code: int
    model: str
    size_bytes: int
    sha256: str
    chunk_size: int
    chunks: tuple[bytes, ...]
    chunk_hashes: tuple[str, ...]

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def chunk_payload(self, index: int, campaign_id: str,
                      content_key: bytes | None = None) -> dict:
        """One wire message carrying one chunk.

        With a content key the payload is AES-256-GCM ciphertext; without one
        it is the raw chunk. The chunk HASH is always of the PLAINTEXT, so the
        integrity guarantee is about the firmware image itself and does not
        change meaning when encryption is switched on. GCM separately
        authenticates the ciphertext in transit.
        """
        if not 0 <= index < self.chunk_count:
            raise IndexError(f"chunk {index} out of range 0..{self.chunk_count - 1}")

        raw = self.chunks[index]
        if content_key is not None:
            from app.core.crypto import encrypt_chunk
            raw = encrypt_chunk(content_key, index, raw, self.firmware_id)

        return {
            "schema": "convoy.chunk.v1",
            "campaign_id": campaign_id,
            "firmware_id": self.firmware_id,
            "index": index,
            "count": self.chunk_count,
            "sha256": self.chunk_hashes[index],
            "encrypted": content_key is not None,
            "data": base64.b64encode(raw).decode("ascii"),
        }


def version_to_code(version: str) -> int:
    """Semantic version -> a single monotonically comparable integer.

    "1.4.0" -> 10400. This is what the device's anti-rollback check compares,
    because comparing version strings lexicographically is a well-known way to
    decide that "1.10.0" is older than "1.9.0".
    """
    parts = version.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"version must be MAJOR.MINOR.PATCH, got {version!r}")
    major, minor, patch = (int(p) for p in parts)
    if not (0 <= minor < 100 and 0 <= patch < 100):
        raise ValueError("minor and patch must each be < 100")
    return major * 10000 + minor * 100 + patch


def safe_version_code(version: str | None) -> int:
    """version_to_code that never raises. Returns 0 on anything unparseable.

    Version strings arriving from devices are untrusted input: a board may be
    running a hand-flashed build called "dev", or nothing at all. Letting a
    malformed string raise inside message ingestion would drop the whole
    message -- including the health data in it -- for a field that is only
    used for comparison.

    0 sorts below every real version, which is the safe default: an unknown
    version is treated as "older than anything", so the device is a candidate
    for an update rather than being silently excluded from one.
    """
    try:
        return version_to_code(version or "")
    except ValueError:
        return 0


def package_firmware(
    data: bytes,
    *,
    firmware_id: str,
    version: str,
    model: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> FirmwarePackage:
    if not data:
        raise ValueError("firmware image is empty")
    if chunk_size < 256:
        raise ValueError("chunk_size is unreasonably small")

    chunks = tuple(data[i:i + chunk_size] for i in range(0, len(data), chunk_size))
    return FirmwarePackage(
        firmware_id=firmware_id,
        version=version,
        version_code=version_to_code(version),
        model=model,
        size_bytes=len(data),
        sha256=sha256_hex(data),
        chunk_size=chunk_size,
        chunks=chunks,
        chunk_hashes=tuple(sha256_hex(c) for c in chunks),
    )


def package_firmware_file(path: str | Path, **kwargs) -> FirmwarePackage:
    return package_firmware(Path(path).read_bytes(), **kwargs)


def build_manifest(
    pkg: FirmwarePackage,
    *,
    device_id: str,
    campaign_id: str,
    min_battery: int,
    min_network_quality: int,
    rollback: bool = False,
    download_url: str | None = None,
    nonce: str | None = None,
    key_wrap: dict | None = None,
) -> dict:
    """Build the per-device manifest that will be signed.

    Note it is built PER DEVICE, not per campaign. That costs one signature per
    device -- microseconds -- and buys the binding that stops a captured
    manifest being replayed across the rest of the fleet.
    """
    return {
        "schema": MANIFEST_SCHEMA,
        "firmware_id": pkg.firmware_id,
        "version": pkg.version,
        "version_code": pkg.version_code,
        "model": pkg.model,
        "size": pkg.size_bytes,
        "sha256": pkg.sha256,
        "chunk_size": pkg.chunk_size,
        "chunk_count": pkg.chunk_count,
        "chunk_hashes": list(pkg.chunk_hashes),
        "device_id": device_id,
        "campaign_id": campaign_id,
        "nonce": nonce or new_nonce(),
        "min_battery": min_battery,
        "min_network_quality": min_network_quality,
        "rollback": rollback,
        "download_url": download_url,
        # Key material lives INSIDE the signed structure. An attacker who could
        # substitute their own ephemeral key and wrapped key would otherwise be
        # able to make a device decrypt chunks of their choosing; the signature
        # binds the key to the same authority that vouches for the image.
        **(key_wrap or {"enc_alg": "none"}),
    }


def assemble(chunks: dict[int, bytes], expected_count: int) -> bytes:
    """Device-side reassembly. Raises if anything is missing."""
    missing = [i for i in range(expected_count) if i not in chunks]
    if missing:
        raise ValueError(f"cannot assemble: missing chunks {missing[:10]}"
                         f"{'...' if len(missing) > 10 else ''}")
    return b"".join(chunks[i] for i in range(expected_count))