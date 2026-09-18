from __future__ import annotations

import logging

from logging_utils import log_event

# Self-managed Level of Assurance. Not IdP-issued (see
# documentation/trackers/security-test-coverage.md). LOA_BASELINE is every
# authenticated OBO caller; LOA_ELEVATED is a caller who has just completed
# the CIBA step-up (see storage/postgres_repo.py:_jwt_for_vault, gated by a
# ciba/<action>/<user> Vault ACL policy switch) for this specific action.
# There is no LOA=3 here — only one elevation mechanism exists, so only one
# elevated level is honest to claim.
LOA_BASELINE = 1
LOA_ELEVATED = 2

# Decision vocabulary. STEP_UP_REQUIRED/ALLOW/DENY match the PDP_Decision
# values a Telefonica-style test catalog expects; EXPIRED is an extension for
# an outcome that isn't a policy denial (the device code's own validity window
# lapsed) but also isn't approval.
STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
ALLOW = "ALLOW"
DENY = "DENY"
EXPIRED = "EXPIRED"


def log_pdp_decision(
    logger: logging.Logger,
    *,
    tool_name: str,
    decision: str,
    current_loa: int,
    required_loa: int,
    **extra,
) -> None:
    """Emit a structured decision log using the field names a Telefonica-style
    security test catalog checks for directly (PDP_Decision, LoA_Level,
    Required_LoA) rather than this codebase's usual snake_case convention —
    deliberate, so these lines are usable as literal audit evidence.
    TransactionID/UserID/Timestamp_UTC are already on every log line via
    bind_log_context (request_id, preferred_username) and log_event's own
    timestamp; not duplicated here."""
    log_event(
        logger,
        "pdp_decision",
        message=f"{tool_name}: {decision} (current_loa={current_loa}, required_loa={required_loa})",
        tool=tool_name,
        PDP_Decision=decision,
        LoA_Level=current_loa,
        Required_LoA=required_loa,
        **extra,
    )
