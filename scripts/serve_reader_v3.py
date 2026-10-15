#!/usr/bin/env python3
"""Loopback reader HTTP supervisor.

Browser -> per-annotation API -> SQLite -> bridge long poll -> host session.
The service owns its bridge child; the bridge owns an independent app-server.
"""
import argparse
import importlib
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from reader_store import ReaderStore, StoreError
from runtime_lock import RuntimeLock

MAX_BODY = 2 * 1024 * 1024


def require_pdf_runtime():
    """Fail before publishing a service that cannot verify PDF evidence.

    Use this interpreter's imports only: another environment's native PDF
    extensions cannot safely be borrowed by adding its site-packages to sys.path.
    """
    try:
        try:
            pdf_module = importlib.import_module("pymupdf")
        except ImportError:
            pdf_module = importlib.import_module("fitz")
        # Also catch an unrelated package named fitz or broken native extension.
        with pdf_module.open() as document:
            document.new_page().get_text()
    except Exception as exc:
        if os.name == "nt":
            command = "& '" + sys.executable.replace("'", "''") + "' -m pip install PyMuPDF"
        else:
            import shlex
            command = shlex.join([sys.executable, "-m", "pip", "install", "PyMuPDF"])
        raise RuntimeError(
            "阅读器启动前检查失败：当前 Python 无法使用 PyMuPDF 验证 source.pdf。\n"
            "实际解释器: " + sys.executable + "\n"
            "请在同一解释器安装依赖后重试，或使用已配置 PyMuPDF 的 Python 运行启动脚本。\n"
            "安装命令: " + command + "\n原因: " + str(exc)
        ) from exc


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

    def __init__(self, vault, paper, port=0, bridge="codex", config=None, translation_runner_factory=None):
        self.runtime_lock = RuntimeLock(Path(vault).resolve() / ".reader" / "service.lock", timeout=0).acquire()
        try:
            self.store = ReaderStore(vault)
            self.store.paper_dir(paper)
        except Exception:
            if hasattr(self, "store"):
                self.store.close()
            self.runtime_lock.close()
            raise
        self.paper = paper
        self.stopping = threading.Event()
        self.token = secrets.token_urlsafe(32)
        self.bridge_token = secrets.token_urlsafe(48)
        self.bridge_kind = bridge
        self.connection_lock = threading.RLock()
        self.service_instance_id = "service_" + uuid.uuid4().hex
        self.bridge_config = config or {}
        self.bridge_process = None
        self.bridge_log = None
        try:
            super().__init__(("127.0.0.1", port), ReaderHandler)
        except Exception:
            self.store.close()
            self.runtime_lock.close()
            raise
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
                    "api_version": 3, "started_at": time.time(), "token": self.token, "service_instance_id": self.service_instance_id})
        atomic_json(self.bridge_config_file, {"url": self.base_url, "token": self.bridge_token,
                    "vault": str(self.store.vault), "adapter": bridge, **self.bridge_config})
        with self.store.transaction():
            # A previous service's heartbeat cannot verify this service's host.
            self.store.db.execute("UPDATE bridges SET seen_at=0")
            if bridge != "none":
                self.store.db.execute("UPDATE assistant_settings SET adapter=? WHERE id=1", (bridge,))
        from translation_jobs import TranslationJobs
        try:
            self.translations = TranslationJobs(self.store, self.bridge_config, translation_runner_factory)
        except Exception:
            self.server_close()
            self.store.close()
            self.runtime_lock.close()
            raise

    def assistant_status(self, paper=None):
        with self.connection_lock:
            connecting = self.bridge_kind != "none" and self.bridge_process is not None and self.bridge_process.poll() is None
            return self.store.status(paper, self.service_instance_id, connecting)["assistant"]

    def _start_bridge_locked(self):
        if self.stopping.is_set() or self.store.settings()["paused"] or self.bridge_kind == "none":
            return False
        if self.bridge_process is not None and self.bridge_process.poll() is None:
            return False
        script = Path(__file__).resolve().parent / "bridge_client.py"
        atomic_json(self.bridge_config_file, {"url": self.base_url, "token": self.bridge_token,
                    "vault": str(self.store.vault), "adapter": self.bridge_kind,
                    "service_instance_id": self.service_instance_id, **self.bridge_config})
        if self.bridge_log is None:
            self.bridge_log = (self.store.state_dir / "bridge.log").open("ab", buffering=0)
        self.bridge_process = subprocess.Popen([sys.executable, str(script), "--config", str(self.bridge_config_file)],
            stdin=subprocess.DEVNULL, stdout=self.bridge_log, stderr=self.bridge_log,
            creationflags=hidden_flags(), start_new_session=(os.name != "nt"))
        return True

    def assistant_action(self, action, data):
        if self.stopping.is_set():
            raise StoreError(409, "service is stopping")
        paper = data.get("paper_id", self.paper)
        if "enable" in data and type(data["enable"]) is not bool:
            raise StoreError(400, "enable must be boolean")
        with self.connection_lock:
            adapter = self.bridge_kind if self.bridge_kind != "none" else self.store.settings()["adapter"]
            intent = self.store.assistant_intent(action, paper if action != "pause" else None,
                                                 data.get("request_id"), adapter, data.get("enable", False))
            if action != "pause" and not self.store.settings()["paused"]:
                self.bridge_kind = adapter
                # Idempotence includes process state: a repeated successful request
                # repairs a disconnected process but never creates a second child.
                self._start_bridge_locked()
            return {"assistant": self.assistant_status(paper), **intent}

    def start_supervisor(self):
        def supervise():
            backoff = 1
            while not self.stopping.is_set():
                self.store.expire_leases()
                if self.bridge_kind != "none" and not self.store.settings()["paused"] and (self.bridge_process is None or self.bridge_process.poll() is not None):
                    if self.bridge_process is not None:
                        if self.stopping.wait(backoff):
                            break
                        backoff = min(30, backoff * 2)
                    with self.connection_lock:
                        self._start_bridge_locked()
                self.stopping.wait(2)
        self.supervisor = threading.Thread(target=supervise, name="reader-supervisor", daemon=True)
        self.supervisor.start()

    def request_stop(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.translations.stop()
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
        self.translations.stop()
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
        self.runtime_lock.close()


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
        if code >= 400:
            # Guards reject before consuming a POST body. Close the connection
            # so those bytes cannot be parsed as a second HTTP request.
            self.close_connection = True
            self.send_header("Connection", "close")
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
                raise StoreError(401, "missing or invalid local token")

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
            if path.startswith("/api/assistant/") and self.command == "POST":
                return self._json(200, self.server.assistant_action(path.rsplit("/", 1)[-1], data))
            if path == "/api/assets" and self.command == "POST":
                from annotation_assets import save_upload
                directory = self.server.store.paper_dir(data.get("paper_id"))
                try:
                    attachment = save_upload(directory, data)
                except ValueError as exc:
                    raise StoreError(400, str(exc)) from exc
                return self._json(201, {"attachment": attachment})
            if path == "/api/stop" and self.command == "POST":
                self._json(200, {"ok": True, "stopping": True})
                self.server.request_stop()
                return
            if path.startswith("/api/bridge/"):
                return self._bridge(path, data)
            bits = path.strip("/").split("/")
            if len(bits) == 4 and bits[:2] == ["api", "papers"] and bits[3] == "translation" and self.command == "POST":
                result = self.server.translations.generate(bits[2], data)
                return self._json(202 if result["status"] in ("queued", "running") else 200, result)
            if len(bits) in (4, 5) and bits[:2] == ["api", "papers"] and bits[3] == "drafts":
                if len(bits) == 4 and self.command == "POST":
                    return self._json(200, self.server.store.save_draft(bits[2], data))
                if len(bits) == 5 and self.command == "DELETE":
                    return self._json(200, self.server.store.delete_draft(bits[2], bits[4], data.get("revision")))
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
                if action in ("followups", "retry", "submit", "cancel"):
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
            return self._json(200, {"ok": True, "paper_id": paper, "api_version": 3, "token": self.server.token,
                                   "service_instance_id": self.server.service_instance_id} if path.endswith("session")
                              else {"ok": True, "paper_id": paper, "api_version": 3, "service_instance_id": self.server.service_instance_id})
        if path == "/api/status":
            paper = query.get("paper_id", [None])[0]
            return self._json(200, {**self.server.store.status(paper), "assistant": self.server.assistant_status(paper), "stopping": self.server.stopping.is_set()})
        if path == "/api/events":
            return self._sse(query)
        bits = path.strip("/").split("/")
        if len(bits) == 4 and bits[:2] == ["api", "papers"] and bits[3] == "translation":
            return self._json(200, self.server.translations.status(bits[2]))
        if len(bits) == 4 and bits[:2] == ["api", "papers"] and bits[3] == "drafts":
            return self._json(200, self.server.store.list_drafts(bits[2]))
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
            settings = store.settings()
            return self._json(200, {**store.heartbeat(bid, data.get("tasks", []), data.get("info")), "stop": self.server.stopping.is_set(), "paused": bool(settings["paused"]), "connection_revision": settings["connection_revision"]})
        if action == "recover":
            return self._json(200, store.recoverable(bid))
        if action == "adopt":
            return self._json(200, store.adopt(data["task_id"], bid))
        if action == "ack":
            return self._json(200, store.acknowledge(data["task_id"], bid, data["claim_token"], data.get("thread_id"), data.get("turn_id")))
        if action == "result":
            return self._json(200, store.result(data["task_id"], bid, data["claim_token"], data.get("result"), data.get("error")))
        if action == "unknown":
            return self._json(200, store.mark_unknown(data["task_id"], bid, data["claim_token"], data.get("error", "delivery outcome unknown")))
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
    parser.add_argument("--model", help="Optional host model override; omitted values inherit host/session settings")
    parser.add_argument("--effort", help="Optional reasoning override; omitted values inherit host/session settings")
    parser.add_argument("--codex-bin", help="Optional installed codex executable")
    args = parser.parse_args()
    try:
        require_pdf_runtime()
    except RuntimeError as exc:
        parser.exit(2, str(exc) + "\n")
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
