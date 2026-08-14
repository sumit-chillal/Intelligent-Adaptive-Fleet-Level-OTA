"""
CONVOY — the shared vocabulary of the system.

Every outcome anywhere in the system resolves to exactly one ReasonCode from
this file. Free-text error strings are banned in persisted data (Rules.md §2)
because the analytics page, the adaptive engine, and the audit trail all have
to group outcomes, and you cannot group on prose.

The single most important distinction in this file is SKIPPED vs FAILED:

    SKIPPED  -> the system correctly refused to attempt an update.
                Refusing to flash a car sitting at 8% battery is the system
                WORKING. It must NOT count toward the failure rate, or the
                adaptive engine will shrink the batch as punishment for its
                own good judgement.

    FAILED   -> an update was attempted and did not complete.
                This is the only category that feeds the failure rate.

Getting this backwards is the most likely silent bug in the project, which is
why `counts_as_failure()` lives here as a single function rather than being
re-derived at each call site.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    # ---- success -----------------------------------------------------------
    SUCCESS = "SUCCESS"

    # ---- not attempted (never counts toward the failure rate) --------------
    SKIPPED_INELIGIBLE_LOW_BATTERY = "SKIPPED_INELIGIBLE_LOW_BATTERY"
    SKIPPED_INELIGIBLE_POOR_NETWORK = "SKIPPED_INELIGIBLE_POOR_NETWORK"
    SKIPPED_ALREADY_ON_TARGET = "SKIPPED_ALREADY_ON_TARGET"
    SKIPPED_OFFLINE = "SKIPPED_OFFLINE"

    # ---- attempted and failed (counts toward the failure rate) -------------
    FAILED_LOW_BATTERY = "FAILED_LOW_BATTERY"
    FAILED_POOR_NETWORK = "FAILED_POOR_NETWORK"
    FAILED_TIMEOUT = "FAILED_TIMEOUT"
    FAILED_CHUNK_HASH_MISMATCH = "FAILED_CHUNK_HASH_MISMATCH"
    FAILED_IMAGE_HASH_MISMATCH = "FAILED_IMAGE_HASH_MISMATCH"
    FAILED_SIGNATURE_INVALID = "FAILED_SIGNATURE_INVALID"
    FAILED_ANTI_ROLLBACK = "FAILED_ANTI_ROLLBACK"
    FAILED_FLASH_WRITE = "FAILED_FLASH_WRITE"
    FAILED_INSUFFICIENT_SPACE = "FAILED_INSUFFICIENT_SPACE"
    FAILED_MAX_ATTEMPTS = "FAILED_MAX_ATTEMPTS"

    # ---- terminal, operator or system driven -------------------------------
    ROLLED_BACK_AUTOMATIC = "ROLLED_BACK_AUTOMATIC"
    ROLLED_BACK_MANUAL = "ROLLED_BACK_MANUAL"
    ABORTED_BY_OPERATOR = "ABORTED_BY_OPERATOR"
    ABORTED_FAILURE_STORM = "ABORTED_FAILURE_STORM"


def counts_as_failure(code: ReasonCode) -> bool:
    """The single authority on what the adaptive engine treats as a failure."""
    return code.startswith("FAILED_") or code == ReasonCode.ROLLED_BACK_AUTOMATIC


def counts_as_success(code: ReasonCode) -> bool:
    return code == ReasonCode.SUCCESS


def is_terminal(code: ReasonCode) -> bool:
    """Terminal outcomes end a device's participation in the current attempt."""
    return True  # every ReasonCode is terminal; retries create a NEW attempt


class DecisionReason(StrEnum):
    """Why the adaptive engine changed (or did not change) the batch size."""

    CANARY = "CANARY"
    SHRINK_HIGH_FAILURE = "SHRINK_HIGH_FAILURE"
    SHRINK_MINOR_FAILURE = "SHRINK_MINOR_FAILURE"
    GROW_STABLE = "GROW_STABLE"
    HOLD_COOLDOWN = "HOLD_COOLDOWN"
    HOLD_AT_MIN = "HOLD_AT_MIN"
    HOLD_AT_MAX = "HOLD_AT_MAX"
    ABORT_FAILURE_STORM = "ABORT_FAILURE_STORM"
    OPERATOR_OVERRIDE = "OPERATOR_OVERRIDE"


class DecisionAction(StrEnum):
    CONTINUE = "CONTINUE"
    ABORT = "ABORT"


class CampaignState(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    ROLLED_BACK = "ROLLED_BACK"


class TargetState(StrEnum):
    """State of one device within one campaign."""

    PENDING = "PENDING"
    OFFERED = "OFFERED"
    DOWNLOADING = "DOWNLOADING"
    INSTALLING = "INSTALLING"
    CONFIRMING = "CONFIRMING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"


TERMINAL_TARGET_STATES = frozenset({
    TargetState.SUCCEEDED,
    TargetState.FAILED,
    TargetState.SKIPPED,
    TargetState.ROLLED_BACK,
})


class FirmwareState(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


class EventType(StrEnum):
    """Append-only event log vocabulary. Never rename one of these; add a new
    value instead, because historical rows are immutable."""

    DEVICE_REGISTERED = "DEVICE_REGISTERED"
    DEVICE_ONLINE = "DEVICE_ONLINE"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    HEALTH_SAMPLE = "HEALTH_SAMPLE"
    CAMPAIGN_CREATED = "CAMPAIGN_CREATED"
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    CAMPAIGN_RESUMED = "CAMPAIGN_RESUMED"
    CAMPAIGN_COMPLETED = "CAMPAIGN_COMPLETED"
    CAMPAIGN_ABORTED = "CAMPAIGN_ABORTED"
    BATCH_OPENED = "BATCH_OPENED"
    BATCH_CLOSED = "BATCH_CLOSED"
    ROLLOUT_DECISION = "ROLLOUT_DECISION"
    ELIGIBILITY_CHECKED = "ELIGIBILITY_CHECKED"
    OFFER_SENT = "OFFER_SENT"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_REJECTED = "OFFER_REJECTED"
    DOWNLOAD_PROGRESS = "DOWNLOAD_PROGRESS"
    DOWNLOAD_RESUMED = "DOWNLOAD_RESUMED"
    INSTALL_STARTED = "INSTALL_STARTED"
    UPDATE_SUCCEEDED = "UPDATE_SUCCEEDED"
    UPDATE_FAILED = "UPDATE_FAILED"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
