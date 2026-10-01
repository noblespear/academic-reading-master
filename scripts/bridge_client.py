#!/usr/bin/env python3
"""Local host bridge worker: register -> long poll -> ack -> heartbeat -> result.

Launch only through serve_reader_v3 supervisor for normal use. HTTP contains
delivery metadata and paper evidence; Codex authentication stays in Codex's
local app-server process, never in browser assets or SQLite.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

from bridges.base import UnknownDelivery


class ReaderAPI:
    def __init__(self, config):
        self.url, self.token = config["url"].rstrip("/"), config["token"]
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("bridge accepts only loopback reader HTTP")
        self.bridge_id = config.get("bridge_id") or "bridge_" + uuid.uuid4().hex
        self.opener = build_opener(ProxyHandler({}))  # Local bearer tokens never enter a configured HTTP proxy.

    def post(self, action, data=None, timeout=35):
        payload = {"bridge_id": self.bridge_id, **(data or {})}
        request = Request(self.url + "/api/bridge/" + action, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json", "X-Reader-Token": self.token})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError("reader HTTP " + str(exc.code) + ": " + exc.read().decode("utf-8", "replace")) from exc


def safe_paper_dir(vault, paper):
    root = (Path(vault).resolve() / "papers").resolve()
    directory = (root / paper).resolve()
    if directory.parent != root or not directory.is_dir():
        raise ValueError("paper path must be an existing direct child of vault/papers")
    return directory


def build_context(directory, task):
    from deploy_reader import load_paper_data
    data = load_paper_data(directory / "paper-data.js")
    # Never interpret paper JS as executable code. This is strict JSON loading.
    context = {"paper_id": data.get("id"), "title_en": data.get("title_en"), "title_cn": data.get("title_cn"),
               "source_refs": data.get("source_refs", {}), "sources": data.get("sources", []),
               "overview": data.get("overview", {}), "explanation": data.get("explanation", {}),
               "sections": data.get("sections", [])}
    context["evidence_contract"] = {"sections": "original English paper and its Chinese translation",
        "overview": "AI-authored overview; follow block.source_refs to original evidence",
        "explanation": "AI-authored explanation/history; historical answers are not primary paper evidence",
        "source_id": "paper", "pdf_file": "source.pdf", "pdf_pages": "one-based physical PDF pages"}
    sources = []
    # New v3 sources may be objects or arrays; only relative in-paper text paths
    # are readable. PDF extraction happens during deployment, outside the model.
    candidates = data.get("sources", [])
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    for source in candidates if isinstance(candidates, list) else []:
        if not isinstance(source, dict):
            continue
        relative = source.get("text_path") or source.get("text_asset")
        if not isinstance(relative, str):
            continue
        path = (directory / relative).resolve()
        try:
            path.relative_to(directory)
        except ValueError:
            raise ValueError("source text path outside paper directory")
        if path.suffix.lower() not in (".txt", ".json", ".md") or not path.is_file():
            continue
        if path.stat().st_size > 2*1024*1024:
            raise ValueError("source text exceeds 2MB evidence limit")
        sources.append({"source_id": source.get("id", "paper"), "text": path.read_text(encoding="utf-8-sig")})
    if sources:
        context["source_texts"] = sources
    if len(json.dumps(context, ensure_ascii=False)) > 1500000:
        raise ValueError("paper context exceeds 1.5MB; prepare narrower source evidence")
    return context


def run(config):
    api = ReaderAPI(config)
    adapter_name = config.get("adapter", "codex")
    if adapter_name == "mock":
        from bridges.mock import MockBridge
        adapter = MockBridge(config)
    elif adapter_name == "codex":
        from bridges.codex_app_server import CodexAppServerBridge
        adapter = CodexAppServerBridge(config)
    else:
        raise ValueError("unknown adapter; implement bridges.base.HostBridge and select it here")
    stopping = False
    last_pulse = 0
    active_task = None

    def pulse(force=False):
        nonlocal stopping, last_pulse
        if not force and time.monotonic() - last_pulse < 4:
            return stopping
        last_pulse = time.monotonic()
        claims = [{"task_id": active_task["id"], "claim_token": active_task["claim_token"]}] if active_task else []
        response = api.post("heartbeat", {"tasks": claims})
        stopping = bool(response.get("stop"))
        return stopping or bool(response.get("controls"))

    def publish(task, result=None, error=None):
        # Retrying the result delivery is safe: store deduplicates terminal tasks.
        # Retrying a host turn/start is deliberately absent here.
        for attempt in range(4):
            try:
                return api.post("result", {"task_id": task["id"], "claim_token": task["claim_token"],
                    "result": result, "error": error})
            except (URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(min(2, attempt + 0.2))

    try:
        host_process = getattr(getattr(adapter, "server", None), "process", None)
        response = api.post("register", {"adapter": adapter_name, "info": {"pid": os.getpid(),
            "host_pid": host_process.pid if host_process else None,
            "model_override": config.get("model"), "effort_override": config.get("effort")}})
        if response.get("stop"):
            return 0
        recovery_checked = set()
        while not stopping:
            pulse(True)
            if stopping:
                break
            recovered = api.post("recover").get("tasks", [])
            for candidate in recovered:
                if candidate["id"] in recovery_checked:
                    continue
                try:
                    task = api.post("adopt", {"task_id": candidate["id"]})["task"]
                except RuntimeError:
                    continue  # Previous bridge may still own a fresh lease.
                recovery_checked.add(task["id"])
                active_task = task
                try:
                    answer = adapter.recover(task, pulse)
                    publish(task, answer)
                except UnknownDelivery as exc:
                    # Leave the acknowledged task unknown. Marking it failed
                    # would unblock automatic successor deliveries too early.
                    print("Recovery pending:", task["id"], str(exc), flush=True)
                except Exception as exc:
                    publish(task, error=str(exc))
                finally:
                    active_task = None
            response = api.post("claim", {"wait_seconds": 20})
            if response.get("stop"):
                stopping = True
                break
            task = response.get("task")
            if not task:
                continue
            active_task = task
            acknowledged = False
            try:
                directory = safe_paper_dir(config["vault"], task["paper_id"])
                context = build_context(directory, task)
                thread_id = adapter.session(directory, task.get("session_thread_id"))
                # Durable ack before turn/start: a crash in the send/response
                # window is reconciled against the thread instead of re-sent.
                api.post("ack", {"task_id": task["id"], "claim_token": task["claim_token"], "thread_id": thread_id})
                acknowledged = True
                task["thread_id"] = thread_id
                turn_id = adapter.start(thread_id, task, context)
                task["turn_id"] = turn_id
                api.post("ack", {"task_id": task["id"], "claim_token": task["claim_token"], "thread_id": thread_id, "turn_id": turn_id})
                answer = adapter.wait(thread_id, turn_id, pulse)
                publish(task, answer)
            except UnknownDelivery as exc:
                print("Delivery pending reconciliation:", task["id"], str(exc), flush=True)
                if not acknowledged:
                    publish(task, error=str(exc))
                if getattr(getattr(adapter, "server", None), "closed", False):
                    raise
                # Acked state is preserved until its lease expires to unknown.
            except Exception as exc:
                print("Task failed:", task["id"], str(exc), flush=True)
                try:
                    publish(task, error=str(exc))
                except Exception:
                    pass
            finally:
                active_task = None
    finally:
        adapter.close()
        try:
            api.post("unregister", timeout=3)
        except (RuntimeError, URLError, OSError):
            pass
    return 0


def main():
    parser = argparse.ArgumentParser(description="Local reader host bridge")
    parser.add_argument("--config", required=True, help="Local private bridge config generated by reader supervisor")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    return run(config)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, URLError) as exc:
        print("Bridge stopped:", str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
