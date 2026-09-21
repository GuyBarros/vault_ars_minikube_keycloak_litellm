"""The Consul mesh's identity namespace, read from Consul itself.

A SPIFFE ID is spiffe://<trust domain>/ns/<namespace>/dc/<datacenter>/svc/<service>.
The trust domain is generated per Consul cluster (a UUID), so it can't be hard-coded.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

UNAVAILABLE = "(unavailable)"  # visibly not a real trust domain / datacenter


def consul_identity(kc: list[str], token_file: Path) -> tuple[str, str]:
    """(trust domain, datacenter) from the Consul server's CA roots and agent config.
    Returns (UNAVAILABLE, UNAVAILABLE) when Consul can't be reached."""
    try:
        token = token_file.read_text().strip()
        proc = subprocess.run(
            kc + ["-n", "consul", "exec", "consul-server-0", "-c", "consul", "--",
                  "env", f"T={token}", "sh", "-c",
                  'for p in connect/ca/roots self; do '
                  'curl -sk -H "X-Consul-Token: $T" https://localhost:8501/v1/agent/$p; echo; done'],
            capture_output=True, text=True, timeout=25, check=False,
        )
        roots, agent = (json.loads(line) for line in proc.stdout.strip().splitlines()[:2])
        return roots["TrustDomain"], agent["Config"]["Datacenter"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return UNAVAILABLE, UNAVAILABLE


def spiffe_id(trust_domain: str, datacenter: str, namespace: str, service: str) -> str:
    return f"spiffe://{trust_domain}/ns/{namespace}/dc/{datacenter}/svc/{service}"
