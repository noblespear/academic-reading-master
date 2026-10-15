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

from bridges.base import UnknownDelivery, ConfirmedFailure
from qa_source_evidence import (pdf_text_evidence, selected_images, walk_blocks, digest_file,
                                readable_block, readable_value, reader_body, json_digest, with_section_sources)

MAX_CONTEXT_BYTES = 1500000


def context_capacity(context):
    encoded = json.dumps(context, ensure_ascii=False)
    ascii_chars = sum(ord(char) < 128 for char in encoded)
    return {"utf8_bytes": len(encoded.encode("utf-8")), "byte_limit": MAX_CONTEXT_BYTES,
            "estimated_tokens": (ascii_chars + 2) // 3 + (len(encoded) - ascii_chars) * 2,
            "token_estimate_method": "conservative heuristic: ASCII/3 + non-ASCII*2; not tokenizer measurement",
            "model_context_window": "unknown until checked by host adapter", "truncated": False}


class ReaderHTTPError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


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
            raise ReaderHTTPError(exc.code, "reader HTTP " + str(exc.code) + ": " + exc.read().decode("utf-8", "replace")) from exc


def safe_paper_dir(vault, paper):
    root = (Path(vault).resolve() / "papers").resolve()
    directory = (root / paper).resolve()
    if directory.parent != root or not directory.is_dir():
        raise ValueError("paper path must be an existing direct child of vault/papers")
    return directory


def build_context(directory, task):
    from deploy_reader import load_paper_data
    directory = Path(directory).resolve()
    data = load_paper_data(directory / "paper-data.js")
    # Never interpret paper JS as executable code. This is strict JSON loading.
    body = reader_body(data)
    context = {"paper_id": data.get("id"), "title_en": data.get("title_en"), "title_cn": data.get("title_cn"),
               "source_refs": data.get("source_refs", {}), "reader_body": body}
    context["reader_snapshot"] = {"content_version": data.get("content_version"),
                                  "paper_data_sha256": digest_file(directory / "paper-data.js"),
                                  "reader_body_sha256": json_digest(body)}
    source_pdf = pdf_text_evidence(directory)
    # Full PDF extraction stays in a hash-bound local cache. Only a source
    # request by the answer model may put original pages into its context.
    context["original_pdf"] = {key: source_pdf[key] for key in
                               ("status", "pdf_sha256", "page_count", "ocr_performed") if key in source_pdf}
    context["original_pdf"]["text_delivery"] = "on request only; no PDF text supplied in this packet"
    context["source_coverage"] = {key: source_pdf[key] for key in
        ("status", "page_count", "covered_pages", "empty_text_pages")}
    if source_pdf.get("pdf_sha256"):
        context["source_coverage"]["pdf_sha256"] = source_pdf["pdf_sha256"]
    context["evidence_contract"] = {"original_pdf": "hash-verified local PDF cache, NOT supplied full text; reader_source can provide bounded original pages/excerpts when necessary; extraction is not OCR and can lose math/layout",
        "reader_body": "readable AI-authored overview and explanation only; source_refs retain evidence identities; translation and component programs are excluded",
        "selected_blocks": "current selected reader prose/formula/figure only; a translation selection does not include the rest of the translation",
        "source_id": "paper", "pdf_file": "source.pdf", "pdf_pages": "one-based physical PDF pages"}
    # Citation identities are useful; arbitrary supplemental text files are not
    # default context and cannot silently substitute for verified original pages.
    candidates = data.get("sources", [])
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    for source in candidates if isinstance(candidates, list) else []:
        if not isinstance(source, dict):
            continue
        identity = {key: source[key] for key in ("id", "source_id", "title", "label", "url", "doi")
                    if isinstance(source.get(key), str)}
        context.setdefault("sources", []).append(identity)
    payload = task.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    anchor = payload.get("anchor", {})
    fragments = anchor.get("fragments") or [anchor]
    context["selected_fields"] = []
    context["selected_blocks"] = []
    views = {"translation": data.get("sections", []), "body": data.get("sections", []),
             "overview": data.get("overview", {}).get("sections", []), "explanation": data.get("explanation", {}).get("sections", [])}
    all_blocks = [with_section_sources(block, section) for view, sections in views.items() if view != "body"
                  for section in sections for block in walk_blocks(section.get("blocks", []))]
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        content_id = fragment.get("content_id") or anchor.get("content_id") or payload.get("block_id")
        view = fragment.get("view") or anchor.get("view", "translation")
        block = find_context_block(views.get(view, []), content_id)
        if block is None:
            continue
        section = next((section for section in views.get(view, [])
                        if find_context_block([section], content_id) is block), {})
        block = with_section_sources(block, section)
        if block not in context["selected_blocks"]:
            context["selected_blocks"].append(block)
        field = fragment.get("field") or anchor.get("field", "text_zh")
        try:
            value = resolve_learning_field(block, field, data.get("learning_catalog", {}), all_blocks)
        except (KeyError, IndexError, ValueError, TypeError, StopIteration):
            continue
        static_code = block.get("type") == "code" and field == "code"
        if static_code or not any(part.lower() in ("html", "css", "js", "javascript", "script", "code") for part in field.split(".")):
            context["selected_fields"].append({"content_id": content_id, "field": field, "value": readable_value(value)})
    # Follow only identifiers present in the current verified reader structure.
    linked = list(context["selected_blocks"])
    for block in linked:
        references = {value for value in (block.get("formula_ref"), block.get("source_formula_id")) if isinstance(value, str)}
        references.update(ref.get("block_id") for ref in block.get("source_refs", []) if isinstance(ref, dict))
        references.update(ref.get("content_id") for ref in block.get("source_refs", []) if isinstance(ref, dict))
        for candidate in all_blocks:
            if (candidate.get("id") in references and candidate.get("type") in ("formula", "figure", "diagram")
                    and candidate not in context["selected_blocks"]):
                context["selected_blocks"].append(candidate)
    images, metadata = selected_images(directory, source_pdf, context["selected_blocks"], all_blocks, payload)
    context["selected_blocks"] = [readable_block(block) for block in context["selected_blocks"]]
    context["selected_source_images"] = images
    context["source_image_metadata_only"] = metadata
    context["context_capacity"] = context_capacity(context)
    if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("readable question context exceeds 1.5MB; no source pages were truncated; narrow the selected reading context")
    return context


def find_context_block(sections, content_id):
    """Keep old anchors usable after a teaching block moves into learning prose."""
    roots = [block for section in sections for block in section.get("blocks", [])]
    direct = next((block for block in roots if block.get("id") == content_id), None)
    if direct is not None:
        return direct
    matches = []
    def walk(blocks):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("id") == content_id:
                matches.append(block)
            if block.get("type") in ("quote", "details"):
                walk(block.get("blocks", []))
            learning = block.get("learning")
            if isinstance(learning, dict):
                walk(learning.get("blocks", []))
    for block in roots:
        if block.get("type") in ("quote", "details"):
            walk(block.get("blocks", []))
        walk(block.get("learning", {}).get("blocks", []))
    # Rich documents may reuse IDs; never assign an old anchor by guesswork.
    return matches[0] if len(matches) == 1 else None


def resolve_learning_field(block, field, catalog, all_blocks=()):
    """Resolve canonical learning paths while retaining the parent block ID."""
    parts, value, index = field.split("."), block, 0
    while index < len(parts):
        part = parts[index]
        if part in ("concept_ids", "aid_ids") and isinstance(value, dict):
            reference = value[part][int(parts[index + 1])]
            collection = "concepts" if part == "concept_ids" else "aids"
            value = next(item for item in catalog.get(collection, []) if item.get("id") == reference)
            index += 2
        elif part == "aid_id" and isinstance(value, dict):
            value = next(item for item in catalog.get("aids", []) if item.get("id") == value["aid_id"])
            index += 1
        elif part == "latex" and isinstance(value, dict) and "latex" not in value and (value.get("formula_ref") or value.get("source_formula_id")):
            reference = value.get("formula_ref") or value.get("source_formula_id")
            value = next(item["latex"] for item in all_blocks if item.get("id") == reference)
            index += 1
        else:
            value = value[int(part)] if isinstance(value, list) else value[part]
            index += 1
    return value


def run(config):
    api = ReaderAPI(config)
    adapter_name = config.get("adapter", "codex")
    api.post("register", {"adapter": adapter_name, "info": {"pid": os.getpid(), "health": "unknown",
             "service_instance_id": config.get("service_instance_id"), "model_override": config.get("model"), "effort_override": config.get("effort")}})
    try:
        if adapter_name == "mock":
            from bridges.mock import MockBridge
            adapter = MockBridge(config)
        elif adapter_name == "codex":
            from bridges.codex_app_server import CodexAppServerBridge
            adapter = CodexAppServerBridge(config)
        else:
            raise ValueError("unknown adapter; implement bridges.base.HostBridge and select it here")
    except Exception as exc:
        try:
            api.post("heartbeat", {"info": {"health": "unavailable", "error": str(exc)}})
            api.post("unregister")
        except (RuntimeError, OSError):
            pass
        raise
    stopping = False
    last_pulse = 0
    active_task = None
    recovery_checked = set()
    connection_revision = None
    recheck_pending = False

    def pulse(force=False):
        nonlocal stopping, last_pulse, connection_revision, recheck_pending
        if not force and time.monotonic() - last_pulse < 4:
            return stopping
        last_pulse = time.monotonic()
        claims = [{"task_id": active_task["id"], "claim_token": active_task["claim_token"]}] if active_task else []
        response = api.post("heartbeat", {"tasks": claims, "info": adapter.health()})
        revision = response.get("connection_revision")
        if connection_revision is not None and revision != connection_revision:
            # A new explicit ensure retries inspection, never turn/start.
            recovery_checked.clear()
            recheck_pending = True
        if recheck_pending and not active_task:
            adapter.recheck()
            recheck_pending = False
        connection_revision = revision
        stopping = bool(response.get("stop"))
        return stopping or bool(response.get("controls"))

    def uncertain(task, error):
        api.post("unknown", {"task_id": task["id"], "claim_token": task["claim_token"], "error": str(error)})

    def publish(task, result=None, error=None):
        # Retrying the result delivery is safe: store deduplicates terminal tasks.
        # Retrying a host turn/start is deliberately absent here.
        for attempt in range(4):
            try:
                return api.post("result", {"task_id": task["id"], "claim_token": task["claim_token"],
                    "result": result, "error": error})
            except ReaderHTTPError as exc:
                if result is not None and exc.status in (400, 413):
                    raise ConfirmedFailure("Completed answer rejected by reader content validation: " + str(exc)) from exc
                raise
            except (URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(min(2, attempt + 0.2))

    try:
        host_process = getattr(getattr(adapter, "server", None), "process", None)
        response = api.post("register", {"adapter": adapter_name, "info": {"pid": os.getpid(),
            "host_pid": host_process.pid if host_process else None,
            "model_override": config.get("model"), "effort_override": config.get("effort"), **adapter.health()}})
        if response.get("stop"):
            return 0
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
                    uncertain(task, exc)
                    adapter.last_error = str(exc)
                except ConfirmedFailure as exc:
                    publish(task, error=str(exc))
                except Exception as exc:
                    # A restore/read error is not proof of non-execution.
                    uncertain(task, exc)
                    adapter.last_error = str(exc)
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
                adapter.last_error = str(exc)
                if not acknowledged:
                    publish(task, error=str(exc))
                else:
                    uncertain(task, exc)
                if getattr(getattr(adapter, "server", None), "closed", False):
                    raise
                # Acked state is preserved until its lease expires to unknown.
            except ConfirmedFailure as exc:
                adapter.last_error = str(exc)
                publish(task, error=str(exc))
            except Exception as exc:
                print("Task failed:", task["id"], str(exc), flush=True)
                adapter.last_error = str(exc)
                try:
                    if acknowledged:
                        uncertain(task, exc)
                    else:
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
