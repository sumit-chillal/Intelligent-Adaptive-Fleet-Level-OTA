"""
CONVOY — wire message schemas.

Every message crossing the broker is validated here before it is allowed to
touch the database. The broker is a public endpoint: anything can publish
anything to it, including malformed JSON, wrong types, and values outside
sensible ranges. Parsing directly into typed models means a bad message
produces one clean rejection log line instead of a KeyError deep inside the
orchestrator during a live demo.

`extra="ignore"` is deliberate: a device running older firmware may omit
fields, and a newer one may add them. Neither should break ingestion. That is
the same forward/backward compatibility discipline a real fleet needs, because
you can never assume every vehicle is on the current agent version.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Envelope(BaseModel):
    """Fields every message carries (Rules.md §4)."""

    model_config = ConfigDict(extra="ignore")

    schema_: str | None = Field(default=None, alias="schema")
    msg_id: str | None = None
    device_id: str | None = None
    ts: float | None = None


class HelloMessage(Envelope):
    device_type: str = "tcu-sim"
    model: str = "tcu-sim-v1"
    hw_rev: str | None = None
    fleet_tag: str | None = None
    current_version: str | None = None
    active_slot: str | None = None
    battery: int | None = None
    network_quality: int | None = None
    failure_profile: dict | None = None
    x25519_public_key: str | None = None
    resume_pending: bool = False
    agent: str | None = None
    trigger: str | None = None

    @field_validator("battery")
    @classmethod
    def _battery_range(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= 100:
            raise ValueError(f"battery out of range: {v}")
        return v

    @field_validator("network_quality")
    @classmethod
    def _network_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 5:
            raise ValueError(f"network_quality out of range: {v}")
        return v


class HealthMessage(Envelope):
    battery: int
    network_quality: int
    uptime_s: int | None = None
    current_version: str | None = None
    device_type: str | None = None
    model: str | None = None

    @field_validator("battery")
    @classmethod
    def _battery_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"battery out of range: {v}")
        return v

    @field_validator("network_quality")
    @classmethod
    def _network_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError(f"network_quality out of range: {v}")
        return v


class StatusMessage(Envelope):
    online: bool
    reason: str | None = None


class PongMessage(Envelope):
    sent_at: float | None = None


class OtaAckMessage(Envelope):
    campaign_id: str
    accepted: bool
    reason_code: str | None = None
    nonce: str | None = None


class OtaProgressMessage(Envelope):
    campaign_id: str
    chunk_index: int
    chunk_count: int
    bytes_received: int | None = None
    percent: float | None = None


class OtaResumeMessage(Envelope):
    campaign_id: str
    last_chunk_index: int
    firmware_id: str | None = None


class OtaResultMessage(Envelope):
    campaign_id: str
    success: bool
    reason_code: str
    version: str | None = None
    active_slot: str | None = None
    chunk_index: int | None = None
    battery: int | None = None
    network_quality: int | None = None