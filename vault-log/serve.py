#!/usr/bin/env python3
"""Live hop viewer: static files + SSE of kubectl logs --follow."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DIR = Path(__file__).resolve().parent
WORKLOADS = (
    ("default", "deploy/web", "web", "web"),
    ("default", "deploy/ai-agent", "ai-agent", "ai-agent"),
    ("default", "deploy/token-exchange", "token-exchange", "token-exchange"),
    ("default", "deploy/litellm-gateway", "litellm-gateway", "litellm-gateway"),
    ("default", "deploy/user-mcp", "user-mcp", "user-mcp"),
    ("default", "deploy/ciba-channel", "ciba-channel", "ciba-channel"),
    ("opa", "deploy/opa-server", "opa", "opa-server"),
    ("vault", "sts/vault", "vault", "vault"),
)
SPIFFE_SERVICES = (
    "default/web",
    "default/ai-agent",
    "default/litellm-gateway",
    "default/user-mcp",
    "default/token-exchange",
    "default/ciba-channel",
    "default/keycloak",
    "opa/opa-service",
    "vault/vault",
)

STOP = threading.Event()
LOCK = threading.Lock()
SUBSCRIBERS: list[queue.Queue] = []
BACKLOG: deque[str] = deque(maxlen=4000)
SNAPSHOT: dict = {}
KC: list[str] = []
SINCE = "15m"


NOISE_MARKERS = (
    "/health/liveliness",
    "/health/readiness",
    "/health/liveness",
    '"level": "DEBUG"',
    '"level":"DEBUG"',
    "chunk: b'",
    "GET /metrics ",
)


def _noise_line(text: str) -> bool:
    return any(marker in text for marker in NOISE_MARKERS)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")


def publish(obj: dict) -> None:
    encoded = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    with LOCK:
        BACKLOG.append(encoded)
        subs = list(SUBSCRIBERS)
    for q in subs:
        try:
            q.put_nowait(encoded)
        except queue.Full:
            pass


def kubectl_json(args: list[str]) -> dict | list | None:
    try:
        proc = subprocess.run(
            KC + args, capture_output=True, text=True, timeout=25, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def build_snapshot() -> dict:
    identities = []
    for spec in SPIFFE_SERVICES:
        ns, svc = spec.split("/", 1)
        identities.append(
            {
                "service": svc,
                "namespace": ns,
                "spiffe_id": f"spiffe://dc1.consul/ns/{ns}/dc/dc1/svc/{svc}",
                "mint": "consul-connect SVID (sidecar consul-dataplane)",
            }
        )
    intentions = []
    doc = kubectl_json(["get", "serviceintentions", "-A", "-o", "json"])
    if isinstance(doc, dict):
        for item in doc.get("items") or []:
            spec = item.get("spec") or {}
            dest = spec.get("destination") or {}
            for src in spec.get("sources") or []:
                intentions.append(
                    {
                        "source": f"{src.get('namespace', 'default')}/{src.get('name')}",
                        "dest": f"{dest.get('namespace', 'default')}/{dest.get('name')}",
                        "action": src.get("action") or spec.get("action") or "allow",
                    }
                )
    return {
        "collected_at": now_iso(),
        "trust_domain": "dc1.consul",
        "identities": identities,
        "intentions": intentions,
        "catalog": None,
    }


def emit_mesh(snapshot: dict) -> None:
    ts = snapshot.get("collected_at") or now_iso()
    for ident in snapshot.get("identities") or []:
        publish(
            {
                "source": "mesh-snapshot",
                "line": json.dumps(
                    {
                        "event": "mesh_svid",
                        "timestamp": ts,
                        "service": ident["service"],
                        "namespace": ident["namespace"],
                        "spiffe_id": ident["spiffe_id"],
                        "mint": ident["mint"],
                        "message": f"Consul Connect SVID for {ident['namespace']}/{ident['service']}",
                    },
                    separators=(",", ":"),
                ),
            }
        )
    for it in snapshot.get("intentions") or []:
        publish(
            {
                "source": "mesh-snapshot",
                "line": json.dumps(
                    {
                        "event": "mesh_intention",
                        "timestamp": ts,
                        "source": it["source"],
                        "dest": it["dest"],
                        "action": it["action"],
                        "message": f"intention {it['action']} {it['source']} -> {it['dest']}",
                    },
                    separators=(",", ":"),
                ),
            }
        )


def follow_workload(ns: str, target: str, container: str, source: str) -> None:
    cmd = KC + [
        "-n",
        ns,
        "logs",
        "-f",
        target,
        "-c",
        container,
        "--timestamps",
        f"--since={SINCE}",
    ]
    while not STOP.is_set():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            publish(
                {
                    "source": source,
                    "line": json.dumps(
                        {
                            "event": "collect_skip",
                            "message": f"kubectl failed: {exc}",
                            "timestamp": now_iso(),
                        },
                        separators=(",", ":"),
                    ),
                }
            )
            STOP.wait(5)
            continue
        assert proc.stdout is not None
        for line in proc.stdout:
            if STOP.is_set():
                break
            text = line.rstrip("\n")
            if text and not _noise_line(text):
                publish({"source": source, "line": text})
        proc.wait()
        STOP.wait(3)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self) -> None:
        if urlparse(self.path).path != "/stream":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/stream":
            self._stream()
            return
        if path == "/health":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.close_connection = False
        super().do_GET()

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=2000)
        with LOCK:
            hist = list(BACKLOG)
            snap = dict(SNAPSHOT)
            SUBSCRIBERS.append(q)
        try:
            self._sse({"snapshot": snap, "live": True})
            for encoded in hist:
                self._raw(encoded)
            while not STOP.is_set():
                try:
                    encoded = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._raw(encoded)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            with LOCK:
                if q in SUBSCRIBERS:
                    SUBSCRIBERS.remove(q)

    def _sse(self, obj: dict) -> None:
        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self._raw(payload)

    def _raw(self, encoded: str) -> None:
        self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
        self.wfile.flush()


def main() -> int:
    global KC, SINCE, SNAPSHOT
    parser = argparse.ArgumentParser(description="Live PEP hop viewer")
    parser.add_argument("--port", type=int, default=8753)
    parser.add_argument("--since", default="15m")
    parser.add_argument("--profile", default=os.environ.get("PROFILE", "local-minikube-demo"))
    args = parser.parse_args()
    SINCE = args.since
    KC = ["kubectl", "--context", args.profile]

    SNAPSHOT = build_snapshot()
    emit_mesh(SNAPSHOT)
    for ns, target, container, source in WORKLOADS:
        threading.Thread(
            target=follow_workload,
            args=(ns, target, container, source),
            name=f"logs-{source}",
            daemon=True,
        ).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"hop viewer ao vivo: http://127.0.0.1:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        STOP.set()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
