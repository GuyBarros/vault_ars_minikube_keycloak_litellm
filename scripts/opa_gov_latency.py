#!/usr/bin/env python3
"""Demo: how long does opa-gov-api take to make a decision?

Calls POST /evaluate on opa-gov-api N times (default 100) with a benign
prompt (expected 200 "allowed"), and prints the round-trip time of each call
plus min/mean/median/p95/max. Each call is the full decision path:
opa-gov-api -> OPA -> back.

Port-forwards svc/opa-gov-api unless --url is given. The port-forward goes
straight to the pod, so the number excludes the Consul mTLS hop.

    python3 scripts/opa_gov_latency.py
    python3 scripts/opa_gov_latency.py -n 500 --url http://localhost:8000
"""
import argparse
import statistics
import subprocess
import time
import urllib.error
import urllib.request

CTX = "local-minikube-demo"

PROMPT = "What is the capital of France?"


def evaluate(url: str, text: str) -> tuple[int, float]:
    """POST *text* to /evaluate; returns (http status, elapsed milliseconds)."""
    req = urllib.request.Request(
        f"{url}/evaluate", data=text.encode(), headers={"Content-Type": "text/plain"}, method="POST"
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.status
        exc.read()
    return status, (time.perf_counter() - start) * 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=100)
    ap.add_argument("--url", help="opa-gov-api base URL; skips the port-forward")
    args = ap.parse_args()

    forward = None
    url = args.url
    if not url:
        forward = subprocess.Popen(
            ["kubectl", "--context", CTX, "port-forward", "svc/opa-gov-api", "18082:8000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        url = "http://localhost:18082"

    times = []
    try:
        evaluate(url, PROMPT)  # warm-up: connection setup, not counted
        print(f"{'#':>4}  {'http':>4}  {'ms':>8}")
        for i in range(args.count):
            status, ms = evaluate(url, PROMPT)
            times.append(ms)
            print(f"{i + 1:>4}  {status:>4}  {ms:>8.1f}")
    finally:
        if forward:
            forward.terminate()

    times_sorted = sorted(times)
    p95 = times_sorted[max(0, int(len(times_sorted) * 0.95) - 1)]
    print(f"\n{len(times)} decisions (ms): min {min(times):.1f}  mean {statistics.mean(times):.1f}  "
          f"median {statistics.median(times):.1f}  p95 {p95:.1f}  max {max(times):.1f}")


if __name__ == "__main__":
    main()
