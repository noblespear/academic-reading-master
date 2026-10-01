#!/usr/bin/env python3
"""Loopback reader HTTP supervisor (stdlib only).

Browser -> per-annotation API -> SQLite -> bridge long poll -> host session.
The service owns its bridge child; the bridge owns an independent app-server.
"""
import argparse
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from reader_store import ReaderStore, StoreError

MAX_BODY = 2 * 1024 * 1024


def hidden_flags(detach=False):
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NO_WINDOW
    if detach:
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return flags


def terminate_tree(process):
    """Last-resort cleanup of the process tree this supervisor created."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=hidden_flags(), timeout=10)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def atomic_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class ReaderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, vault, paper, port=0, bridge="codex", config=None):
        self.store = ReaderStore(vault)
        self.store.paper_dir(paper)
        self.paper = paper
        self.stopping = threading.Event()
        self.token = secrets.token_urlsafe(32)
        self.bridge_token = secrets.token_urlsafe(48)
        self.bridge_kind = bridge
        self.bridge_config = config or {}
        self.bridge_process = None
        self.bridge_log = None
        super().__init__(("127.0.0.1", port), ReaderHandler)
        self.base_url = "http://127.0.0.1:" + str(self.server_address[1])
        self.state_file = self.store.state_dir / "server.json"
        self.bridge_config_file = self.store.state_dir / "bridge-config.json"
        for directory in (self.store.vault / "papers").iterdir():
            if directory.is_dir() and (directory / "paper-data.js").exists():
                try:
                    from deploy_reader import load_paper_data
                    data = load_paper_data(directory / "paper-data.js")
                    if data.get("schema_version", 1) >= 3:
                        self.store.import_legacy(directory.name)
                except (ValueError, StoreError, OSError) as exc:
                    print("Import skipped:", directory.name, str(exc), file=sys.stderr)
        atomic_json(self.state_file, {"pid": os.getpid(), "url": self.base_url, "paper_id": paper,
                    "api_version": 3, "started_at": time.time(), "token": self.token})
        atomic_json(self.bridge_config_file, {"url": self.base_url, "token": self.bridge_token,
                    "vault": str(self.store.vault), "adapter": bridge, **self.bridge_config})

    def start_supervisor(self):
        def supervise():
            backoff = 1
            while not self.stopping.is_set():
                self.store.expire_leases()
                if self.bridge_kind != "none" and (self.bridge_process is None or self.bridge_process.poll() is not None):
                    if self.bridge_process is not None:
                        if self.stopping.wait(backoff):
                            break
                        backoff = min(30, backoff * 2)
                    script = Path(__file__).resolve().parent / "bridge_client.py"
                    if self.bridge_log is None:
                        self.bridge_log = (self.store.state_dir / "bridge.log").open("ab", buffering=0)
                    self.bridge_process = subprocess.Popen([sys.executable, str(script), "--config", str(self.bridge_config_file)],
                        stdin=subprocess.DEVNULL, stdout=self.bridge_log, stderr=self.bridge_log,
                        creationflags=hidden_flags(), start_new_session=(os.name != "nt"))
                self.stopping.wait(2)
        self.supervisor = threading.Thread(target=supervise, name="reader-supervisor", daemon=True)
        self.supervisor.start()

    def request_stop(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        with self.store.transaction():
            self.store._event(None, "status", {"stopping": True})
        def shutdown():
            # Leave HTTP up briefly so the bridge sees stop and interrupts its turn.
            if self.bridge_process:
                try:
                    self.bridge_process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    terminate_tree(self.bridge_process)
                    try:
                        self.bridge_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.bridge_process.kill()
            self.shutdown()
        threading.Thread(target=shutdown, daemon=True).start()

    def cleanup(self):
        self.stopping.set()
        if hasattr(self, "supervisor"):
            self.supervisor.join(timeout=3)
        self.server_close()
        if self.bridge_log:
            self.bridge_log.close()
        try:
            existing = json.loads(self.state_file.read_text(encoding="utf-8"))
            if existing.get("pid") == os.getpid():
                atomic_json(self.state_file, {"pid": None, "url": self.base_url, "api_version": 3, "stopped": True})
                self.bridge_config_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self.store.close()


class ReaderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # Tokens never enter request logs.

    def _json(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _guard(self, path, query):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in ("127.0.0.1:" + str(port), "localhost:" + str(port)):
            raise StoreError(403, "invalid Host")
        origin = self.headers.get("Origin")
        if origin and origin not in (self.server.base_url, "http://localhost:" + str(port)):
            raise StoreError(403, "cross-origin requests are forbidden")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise StoreError(403, "cross-site requests are forbidden")
        if path.startswith("/api/") and path not in ("/api/session", "/api/ping"):
            expected = self.server.bridge_token if path.startswith("/api/bridge/") else self.server.token
            token = self.headers.get("X-Reader-Token", "")
            if path == "/api/events":
                token = token or query.get("token", [""])[0]
            if not secrets.compare_digest(token, expected):
                raise StoreError(403, "missing or invalid local token")

    def _body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise StoreError(415, "application/json required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise StoreError(400, "invalid length")
        if not 0 < length <= MAX_BODY:
            raise StoreError(413, "invalid body size")
        try:
            data = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, ValueError):
            raise StoreError(400, "invalid JSON")
        if not isinstance(data, dict):
            raise StoreError(400, "JSON object required")
        return data

    def _dispatch(self):
        try:
            parsed = urlparse(self.path)
            path, query = unquote(parsed.path), parse_qs(parsed.query)
            self._guard(path, query)
            if self.command in ("GET", "HEAD"):
                return self._get(path, query)
            data = self._body()
            if path == "/api/stop" and self.command == "POST":
                self._json(200, {"ok": True, "stopping": True})
                self.server.request_stop()
                return
            if path.startswith("/api/bridge/"):
                return self._bridge(path, data)
            bits = path.strip("/").split("/")
            if len(bits) < 4 or bits[:2] != ["api", "papers"] or bits[3] != "annotations":
                raise StoreError(404, "route not found")
            paper, store = bits[2], self.server.store
            store.paper_dir(paper)
            if len(bits) == 4 and self.command == "POST":
                return self._json(201, store.create(paper, data))
            if len(bits) == 5:
                if self.command == "PATCH":
                    return self._json(200, store.edit(paper, bits[4], data))
                if self.command == "DELETE":
                    return self._json(200, store.delete(paper, bits[4], data.get("revision")))
            if len(bits) == 6 and self.command == "POST":
                aid, action = bits[4], bits[5]
                if action in ("followups", "retry", "submit"):
                    return self._json(200, store.edit(paper, aid, {**data, "submit": True}, "followup" if action == "followups" else action))
                if action == "read":
                    return self._json(200, store.mark_read(paper, aid, data))
            raise StoreError(404, "route not found")
        except StoreError as exc:
            return self._json(exc.status, {"ok": False, "error": str(exc), "current": exc.current})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print("HTTP handler:", repr(exc), file=sys.stderr)
            return self._json(500, {"ok": False, "error": "internal service error"})

    do_GET = do_HEAD = do_POST = do_PATCH = do_DELETE = _dispatch

    def _get(self, path, query):
        if path in ("/api/session", "/api/ping"):
            paper = query.get("paper_id", [self.server.paper])[0]
            self.server.store.paper_dir(paper)
            return self._json(200, {"ok": True, "paper_id": paper, "api_version": 3, "token": self.server.token} if path.endswith("session")
                              else {"ok": True, "paper_id": paper, "api_version": 3})
        if path == "/api/status":
            return self._json(200, {**self.server.store.status(), "stopping": self.server.stopping.is_set()})
        if path == "/api/events":
            return self._sse(query)
        bits = path.strip("/").split("/")
        if len(bits) == 4 and bits[:2] == ["api", "papers"] and bits[3] == "annotations":
            return self._json(200, self.server.store.list_annotations(bits[2]))
        if path.startswith("/api/"):
            raise StoreError(404, "route not found")
        self._static(path)

    def _bridge(self, path, data):
        if self.command != "POST":
            raise StoreError(405, "POST required")
        store, bid = self.server.store, data.get("bridge_id")
        action = path.rsplit("/", 1)[-1]
        if self.server.stopping.is_set() and action not in ("heartbeat", "result", "unregister"):
            return self._json(200, {"stop": True, "task": None})
        if action == "register":
            return self._json(200, store.register_bridge(bid, data.get("adapter", "custom"), data.get("info")))
        if action == "unregister":
            return self._json(200, store.unregister_bridge(bid))
        if action == "claim":
            deadline = time.monotonic() + max(0, min(25, float(data.get("wait_seconds", 20))))
            while not self.server.stopping.is_set():
                claim = store.claim(bid)
                if claim["task"] or time.monotonic() >= deadline:
                    return self._json(200, claim)
                with store.changed:
                    store.changed.wait(min(2, max(0, deadline - time.monotonic())))
            return self._json(200, {"stop": True, "task": None})
        if action == "heartbeat":
            return self._json(200, {**store.heartbeat(bid, data.get("tasks", [])), "stop": self.server.stopping.is_set()})
        if action == "recover":
            return self._json(200, store.recoverable(bid))
        if action == "adopt":
            return self._json(200, store.adopt(data["task_id"], bid))
        if action == "ack":
            return self._json(200, store.acknowledge(data["task_id"], bid, data["claim_token"], data.get("thread_id"), data.get("turn_id")))
        if action == "result":
            return self._json(200, store.result(data["task_id"], bid, data["claim_token"], data.get("result"), data.get("error")))
        raise StoreError(404, "bridge route not found")

    def _sse(self, query):
        paper = query.get("paper_id", [None])[0]
        if paper:
            self.server.store.paper_dir(paper)
        try:
            seq = int(self.headers.get("Last-Event-ID") or query.get("after", [self.server.store.event_seq()])[0])
        except ValueError:
            raise StoreError(400, "invalid event cursor")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b": ready\n\n")
        self.wfile.flush()
        try:
            while not self.server.stopping.is_set():
                events = self.server.store.events_after(seq, paper)
                for event in events:
                    raw = ("id: %d\nevent: %s\ndata: %s\n\n" % (event["seq"], event["kind"], json.dumps(event["data"], ensure_ascii=False))).encode("utf-8")
                    self.wfile.write(raw)
                    seq = event["seq"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                with self.server.store.changed:
                    self.server.store.changed.wait(5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _static(self, path):
        bits = path.lstrip("/").split("/")
        if len(bits) >= 2 and bits[0] == "papers":
            paper = bits[1]
            relative = "/".join(bits[2:]) or "reader.html"
        else:
            paper, relative = self.server.paper, path.lstrip("/") or "reader.html"
        root = self.server.store.paper_dir(paper)
        if any(part.startswith(".") for part in relative.replace("\\", "/").split("/")):
            raise StoreError(403, "hidden/traversal paths forbidden")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise StoreError(403, "path outside paper directory")
        if target.name == "annotations.json" or target.suffix.lower() in (".sqlite3", ".bat", ".cmd", ".py"):
            raise StoreError(403, "private file")
        if not target.is_file():
            raise StoreError(404, "file not found")
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser(description="Reader v3 loopback service and host bridge supervisor")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--bridge", choices=("codex", "mock", "none"), default="codex")
    parser.add_argument("--model", help="Optional host model override; default inherits host")
    parser.add_argument("--effort", help="Optional host reasoning override; default inherits host")
    parser.add_argument("--codex-bin", help="Optional installed codex executable")
    args = parser.parse_args()
    config = {key: getattr(args, key) for key in ("model", "effort", "codex_bin") if getattr(args, key)}
    server = ReaderServer(args.vault, args.paper, args.port, args.bridge, config)
    server.start_supervisor()
    url = server.base_url + "/papers/" + args.paper + "/reader.html"
    print("Reader v3 ready:", url, flush=True)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        server.request_stop()
        if server.bridge_process:
            try:
                server.bridge_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                terminate_tree(server.bridge_process)
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
