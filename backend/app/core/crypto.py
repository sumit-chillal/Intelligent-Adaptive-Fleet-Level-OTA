"""
CONVOY — firmware provenance.

THE REQUIREMENT
---------------
"A device must be able to verify that firmware genuinely originated from the
authorised server before installing it. Make it infeasible for an attacker who
does not hold the server's signing secret to push accepted firmware."

THE THREAT MODEL
----------------
We assume the attacker is as strong as it is realistic to be:

  * They can read and write ANY MQTT topic on the broker.
  * They can replay any message they have ever seen.
  * They can impersonate the broker entirely, or the broker operator is hostile.
  * They can reboot, power-cycle, and physically hold the device.
  * They do NOT hold the server's Ed25519 private key.

Note what is NOT assumed: we do not assume TLS is intact, and we do not assume
the broker is honest. The security of firmware installation must not depend on
the transport, because the transport is a third-party cloud service. TLS gives
us confidentiality and stops casual interception; it is defence in depth, not
the defence.

THE MECHANISM
-------------
Every offer carries a MANIFEST, and the manifest is signed with Ed25519 over
its canonical byte encoding. The device holds only the 32-byte PUBLIC key,
compiled into its image at provisioning time.

The manifest binds, in one signed structure:

    firmware_id, version, version_code   what is being installed
    sha256, chunk_count, chunk_hashes    exactly which bytes are legitimate
    device_id                            WHO this offer is for
    campaign_id, nonce                   WHICH rollout, and freshness

WHY EACH BINDING EXISTS -- each one closes a specific attack:

  sha256 + chunk_hashes
      Closes payload substitution. An attacker who swaps the binary but keeps a
      genuine signature fails at the first chunk hash check. The device
      verifies every chunk on arrival and the whole image before marking the
      slot bootable, so a corrupted image can never be booted.

  device_id
      Closes lateral replay. Without it, a manifest legitimately issued to
      tcu_B_001 could be captured and replayed at every other device in the
      fleet. With it, each device rejects any manifest not addressed to it.

  campaign_id + nonce
      Closes temporal replay. An old, genuine, correctly signed manifest for
      firmware 1.2.0 cannot be replayed to drag a device backwards, because the
      device has already seen that campaign and the nonce will not match a
      pending offer.

  version_code + the device's stored min_allowed_version
      Closes the downgrade attack, which is the subtle one. An attacker who
      cannot forge a signature can still replay a genuine OLD manifest to push
      a device back to a version with a known, patched vulnerability. The
      device therefore refuses any version_code below its stored floor UNLESS
      the manifest carries rollback=true -- and since `rollback` is inside the
      signed structure, only the real server can authorise a downgrade.

WHY FORGERY IS INFEASIBLE
-------------------------
To have a device accept firmware, the attacker must produce a valid Ed25519
signature over a manifest containing a hash of their own image. Ed25519 targets
the ~128-bit security level; there is no known attack materially better than
brute force. The private key never leaves the admin machine -- it is not in
git, not in any Docker image, not in the dashboard bundle, and not on any
device. Devices are given only the public half, which is safe to publish.

WHY Ed25519 AND NOT RSA
-----------------------
The verification has to run on an ESP32 at 240 MHz inside the OTA time budget.
Ed25519 verification is roughly 2 ms there and the public key is 32 bytes.
RSA-2048 is an order of magnitude slower with far larger key material. The
choice is driven by the constrained device, not by fashion.

WHY CANONICAL ENCODING MATTERS
------------------------------
A signature covers BYTES, not a concept. If the server signs
`{"a":1,"b":2}` and the device re-serialises the parsed object as
`{"b":2,"a":1}` before verifying, the signature fails on a perfectly valid
manifest -- and the usual "fix" is to disable verification, which destroys the
entire security property. So both sides agree on one encoding: JSON with sorted
keys, no whitespace, UTF-8. The ESP32 implementation verifies against the raw
received bytes rather than re-serialising, which is the more robust discipline.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MANIFEST_SCHEMA = "convoy.manifest.v1"


def canonical_bytes(manifest: dict) -> bytes:
    """The exact bytes that get signed and verified. Both sides must agree."""
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_nonce() -> str:
    """Cryptographically random, not a timestamp.

    A timestamp-based freshness check would need synchronised clocks across
    laptops in different cities and an ESP32 with no RTC. A server-issued
    random nonce that the device echoes back needs neither.
    """
    return secrets.token_hex(16)


# ---------------------------------------------------------------- key handling
def load_private_key(path_or_pem: str | Path | bytes) -> Ed25519PrivateKey:
    data = (path_or_pem if isinstance(path_or_pem, bytes)
            else Path(path_or_pem).read_bytes() if Path(str(path_or_pem)).exists()
            else str(path_or_pem).encode())
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("signing key must be Ed25519")
    return key


def load_public_key(path_or_pem: str | Path | bytes) -> Ed25519PublicKey:
    data = (path_or_pem if isinstance(path_or_pem, bytes)
            else Path(path_or_pem).read_bytes() if Path(str(path_or_pem)).exists()
            else str(path_or_pem).encode())
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("verification key must be Ed25519")
    return key


def public_key_raw(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


# ------------------------------------------------------------------ signing --
@dataclass(frozen=True)
class SignedManifest:
    manifest: dict
    signature: bytes

    def to_wire(self) -> dict:
        """What actually goes on the MQTT topic."""
        return {
            "manifest": self.manifest,
            "signature": self.signature.hex(),
            "sig_alg": "ed25519",
        }


def sign_manifest(manifest: dict, private_key: Ed25519PrivateKey) -> SignedManifest:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest.schema must be {MANIFEST_SCHEMA!r}")
    for required in ("firmware_id", "version_code", "sha256", "chunk_hashes",
                     "device_id", "campaign_id", "nonce"):
        if required not in manifest:
            raise ValueError(f"manifest is missing required binding: {required!r}")
    return SignedManifest(manifest, private_key.sign(canonical_bytes(manifest)))


class ManifestRejected(Exception):
    """Raised with the ReasonCode-compatible name of why verification failed."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def verify_manifest(
    wire: dict,
    public_key: Ed25519PublicKey,
    *,
    expected_device_id: str,
    expected_campaign_id: str | None = None,
    expected_nonce: str | None = None,
    min_allowed_version_code: int = 0,
) -> dict:
    """Device-side verification. Returns the manifest, or raises ManifestRejected.

    The order of checks is deliberate: the signature is verified FIRST, before
    any field is trusted for a decision. Reading `manifest["device_id"]` and
    acting on it before checking the signature would mean acting on attacker-
    controlled data.
    """
    manifest = wire.get("manifest")
    signature_hex = wire.get("signature")
    if not isinstance(manifest, dict) or not isinstance(signature_hex, str):
        raise ManifestRejected("FAILED_SIGNATURE_INVALID", "malformed offer envelope")
    if wire.get("sig_alg") != "ed25519":
        raise ManifestRejected("FAILED_SIGNATURE_INVALID",
                               f"unsupported algorithm {wire.get('sig_alg')!r}")

    # 1. Provenance, before anything else.
    try:
        public_key.verify(bytes.fromhex(signature_hex), canonical_bytes(manifest))
    except (InvalidSignature, ValueError) as exc:
        raise ManifestRejected("FAILED_SIGNATURE_INVALID", str(exc) or "bad signature")

    # 2. Addressed to this device.
    if manifest.get("device_id") != expected_device_id:
        raise ManifestRejected(
            "FAILED_SIGNATURE_INVALID",
            f"manifest addressed to {manifest.get('device_id')!r}, not us")

    # 3. Freshness.
    if expected_campaign_id and manifest.get("campaign_id") != expected_campaign_id:
        raise ManifestRejected("FAILED_SIGNATURE_INVALID", "campaign mismatch (replay?)")
    if expected_nonce and manifest.get("nonce") != expected_nonce:
        raise ManifestRejected("FAILED_SIGNATURE_INVALID", "nonce mismatch (replay?)")

    # 4. Anti-rollback.
    version_code = int(manifest.get("version_code", -1))
    if version_code < min_allowed_version_code and not manifest.get("rollback", False):
        raise ManifestRejected(
            "FAILED_ANTI_ROLLBACK",
            f"version_code {version_code} is below the floor "
            f"{min_allowed_version_code} and rollback was not authorised")

    return manifest
