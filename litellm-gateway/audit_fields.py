"""CT-06.1 names added beside the PEP's own decision fields.

The PEP decides. This only copies request_id, preferred_username, caller and
the decision it already logged into the caderno names. PDP_Decision stays
whatever the PEP wrote (ALLOW, DENY, STEP_UP_REQUIRED, EXPIRED).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def stamp_caderno_audit(payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("event") or "")
    audit = (
        "PDP_Decision" in payload
        or event == "pdp_decision"
        or event.startswith("token_chain")
    )
    if not audit:
        return payload
    out = dict(payload)
    reason = str(out.get("reason") or out.get("error") or "-")
    package = str(out.get("pdp_package") or event or "pep")
    loa = out.get("LoA_Level")
    if loa is None:
        loa = 1
    out["LoA_Level"] = int(loa) if isinstance(loa, str) and loa.isdigit() else loa
    out["TransactionID"] = str(out.get("request_id") or out.get("TransactionID") or "-")
    out["UserID"] = str(
        out.get("preferred_username") or out.get("UserID") or out.get("login_hint") or "-"
    )
    out["Workload_mTLS_CN"] = str(
        out.get("Workload_mTLS_CN") or out.get("caller") or out.get("agent_id") or "-"
    )
    out["PDP_Decision_ID"] = str(out.get("PDP_Decision_ID") or f"{package}:{reason}")
    if not out.get("Timestamp_UTC"):
        out["Timestamp_UTC"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out
