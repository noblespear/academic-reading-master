"""Installed Codex app-server stdio adapter for answer-only reader turns.

Protocol checked against `codex app-server generate-json-schema` on 2026-10-01:
initialize -> initialized; thread/start|resume; turn/start(outputSchema);
item/completed; turn/completed; turn/interrupt; thread/read(includeTurns).
"""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from .base import HostBridge, UnknownDelivery, ConfirmedFailure
from annotation_assets import MAX_IMAGE_BYTES, verify_image
from qa_source_evidence import (digest_file, confined, json_digest, readable_block,
                                requested_pdf_evidence, reader_body)


ANSWER_SCHEMA = {
    "type": "object", "properties": {
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object", "properties": {
                "label": {"type": "string"}, "locator": {"type": "string"},
                "source_id": {"type": "string"}, "content_id": {"type": "string"},
                "url": {"type": "string"}, "page": {"type": ["integer", "null"]}},
            "required": ["label", "locator", "source_id", "content_id", "url", "page"],
            "additionalProperties": False}}},
    "required": ["answer", "sources"], "additionalProperties": False}


# Kept as compatibility names for callers that imported the old constants.
# Model and reasoning defaults belong to the host/session, so neither value is
# sent unless the user explicitly configures that override.
DEFAULT_ANSWER_MODEL = None
DEFAULT_ANSWER_EFFORT = None
NOTE_IMAGE_TYPES = frozenset({"figure", "diagram"})
MAX_TURN_SOURCE_BYTES = 1500000
SOURCE_TOOL = {"type": "function", "name": "reader_source",
    "description": "Only when current reader prose cannot establish an original paper claim: request up to 3 one-based physical source.pdf pages OR a short literal query. The host returns hash-verified exact text and locators. No paths, URLs or commands. Request page images only when layout/math is needed.",
    "inputSchema": {"type": "object", "properties": {
        "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 3},
        "query": {"type": "string", "maxLength": 160}, "include_images": {"type": "boolean"}},
        "additionalProperties": False}}
PACKET_PREFIX = "Paper context and question are JSON source data:\n"
TASK_PREFIX = "Reader task identity (do not alter): "
SHARED_CONTEXT_FIELDS = ("paper_id", "title_en", "title_cn", "source_refs", "sources",
                         "reader_snapshot", "original_pdf", "source_coverage", "evidence_contract")


def _body_key(block):
    return json.dumps([block.get("view"), block.get("section_id"), block.get("parent_ids", []), block.get("id")], ensure_ascii=False)


def _history_packet(text):
    if not isinstance(text, str) or PACKET_PREFIX not in text:
        return None
    try:
        packet, _end = json.JSONDecoder().raw_decode(text.split(PACKET_PREFIX, 1)[1])
        return packet if isinstance(packet, dict) else None
    except (ValueError, TypeError):
        return None


def _readable_note(document):
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        return {"version": 1, "blocks": []}
    blocks = []
    for block in document["blocks"]:
        if not isinstance(block, dict):
            continue
        item = readable_block(block)
        if block.get("type") in ("details", "quote"):
            item["blocks"] = _readable_note({"blocks": block.get("blocks", [])})["blocks"]
        blocks.append(item)
    return {"version": document.get("version", 1), "blocks": blocks}


def _compact_task(payload, seen_tasks):
    """Current question plus at most two previously unseen plain exchanges."""
    allowed = ("paper_id", "annotation_id", "revision", "user_note", "question", "selected_text",
               "anchor", "block_id", "purpose", "request_id", "attachments")
    result = {key: payload[key] for key in allowed if key in payload}
    if "note_content" in payload:
        result["note_content"] = _readable_note(payload["note_content"])
    history = [item for item in payload.get("history", []) if isinstance(item, dict)
               and _exchange_key(item) not in seen_tasks and item.get("answer")]
    brief = []
    for item in history[-2:]:
        saved = {key: item[key] for key in ("task_id", "user_note", "answer", "sources") if key in item}
        for key in ("user_note", "answer"):
            if isinstance(saved.get(key), str) and len(saved[key]) > 6000:
                saved[key] = saved[key][:6000] + "\n[earlier exchange excerpt; remaining text omitted]"
        brief.append(saved)
    if brief:
        result["history"] = brief
    return result


def _exchange_key(item):
    return item.get("task_id") or "exchange:" + json_digest({
        key: item.get(key)[:6000] if isinstance(item.get(key), str) else item.get(key)
        for key in ("user_note", "answer", "sources")})


def _rollout_path(thread):
    """Only the host-selected, identity-checked session rollout may be read."""
    if not isinstance(thread.get("path"), str):
        return None
    def canonical(value):
        value = str(value)
        # app-server returns Windows verbatim paths (\\?\C:\...). pathlib
        # retains that namespace, so compare both sides in the same namespace.
        if os.name == "nt" and value.startswith("\\\\?\\"):
            value = "\\\\" + value[8:] if value[4:8].upper() == "UNC\\" else value[4:]
        return Path(value).resolve()
    codex_root = canonical(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = canonical(thread["path"])
    if not path.is_relative_to(codex_root / "sessions") or path.suffix != ".jsonl":
        return None
    try:
        with path.open("r", encoding="utf-8") as rollout:
            record = json.loads(rollout.readline(1024 * 1024))
        if record.get("type") == "session_meta" and record.get("payload", {}).get("id") == thread.get("id"):
            return path
    except (OSError, ValueError, TypeError):
        pass
    return None


def _rollout_packets(thread):
    """Reconstruct delivery even when the RPC view follows a compaction.

    Read public user-message records only, never reasoning or summaries. This
    is evidence of delivery, not an attempt to restore a model's context window.
    """
    path = _rollout_path(thread)
    if path is None:
        return None
    packets = []
    try:
        with path.open("r", encoding="utf-8") as rollout:
            for line in rollout:
                # A bounded reader turn can expand under JSON escaping. Ignore
                # unrelated large records without decoding their opaque fields.
                if len(line) > 16 * 1024 * 1024 or '"response_item"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # A final partial write does not establish delivery.
                payload = record.get("payload", {})
                if record.get("type") != "response_item" or payload.get("role") != "user":
                    continue
                for content in payload.get("content", []):
                    text = content.get("text") if isinstance(content, dict) else None
                    if _history_packet(text):
                        packets.append(text)
        return packets
    except OSError:
        return None


def _thread_packets(thread):
    for turn in thread.get("turns", []):
        for item in turn.get("items", []):
            if item.get("type") == "userMessage":
                for content in item.get("content", []):
                    if isinstance(content, dict) and _history_packet(content.get("text")):
                        yield content["text"]


def _delivered_state(texts):
    """Replay complete snapshots/deltas and validate the resulting body hash."""
    state = {"blocks": {}, "seen_tasks": set(), "body_sha256": None, "context_fingerprints": {}}
    body, body_seen = {}, False
    for text in texts:
        packet = _history_packet(text)
        if not packet:
            continue
        marker = text.split("\n", 1)[0]
        if marker.startswith(TASK_PREFIX):
            state["seen_tasks"].add(marker[len(TASK_PREFIX):])
        task = packet.get("task", {})
        state["seen_tasks"].update(_exchange_key(item) for item in task.get("history", [])
                                   if isinstance(item, dict) and item.get("answer"))
        context = packet.get("context", {})
        update = context.get("reader_delivery", {})
        state["context_fingerprints"].update({key: json_digest(context[key]) for key in SHARED_CONTEXT_FIELDS if key in context})
        for key in update.get("removed_context_fields", []):
            state["context_fingerprints"].pop(key, None)
        if isinstance(context.get("original_pdf"), dict):
            state["original_pdf"] = context["original_pdf"]
        if update.get("protocol") != 1:
            # Older host conversations carried the full reader under view keys.
            blocks = context.get("reader_body")
            if blocks is None and any(isinstance(context.get(view), dict) for view in ("overview", "explanation")):
                blocks = reader_body(context)
            if isinstance(blocks, list):
                body_seen = True
                body = {_body_key(block): block for block in blocks if isinstance(block, dict)}
                state["body_sha256"] = json_digest(list(body.values()))
            continue
        body_seen = True
        blocks = context.get("reader_body", [])
        valid = isinstance(blocks, list) and all(isinstance(block, dict) for block in blocks)
        if update.get("mode") == "snapshot":
            candidate = {}
        elif update.get("mode") == "delta" and state["body_sha256"] is not None and update.get("base_body_sha256") == state["body_sha256"]:
            candidate = dict(body)
        else:
            valid, candidate = False, {}
        if valid:
            candidate.update({_body_key(block): block for block in blocks})
            for key in update.get("removed_blocks", []):
                candidate.pop(key, None)
            order = update.get("block_order")
            if order is not None:
                valid = (isinstance(order, list) and all(isinstance(key, str) for key in order)
                         and len(order) == len(candidate) and set(order) == set(candidate))
                if valid:
                    candidate = {key: candidate[key] for key in order}
            valid = valid and json_digest(list(candidate.values())) == update.get("body_sha256")
        body = candidate if valid else {}
        state["body_sha256"] = update.get("body_sha256") if valid else None
    state["blocks"] = {key: json_digest(block) for key, block in body.items()}
    state["baseline_status"] = "verified" if state["body_sha256"] is not None else "unverified" if body_seen else "uninitialized"
    return state


def _resumed_source_tool(thread):
    """Verify the persisted host tool declaration; legacy threads lack it."""
    path = _rollout_path(thread)
    if path is None:
        return False
    try:
        with path.open("r", encoding="utf-8") as rollout:
            line = rollout.readline(1024 * 1024)
        record = json.loads(line)
        meta = record.get("payload", {})
        return (record.get("type") == "session_meta" and meta.get("id") == thread.get("id")
                and any(isinstance(tool, dict) and tool.get("type") == "function"
                        and tool.get("name") == SOURCE_TOOL["name"]
                        and tool.get("inputSchema") == SOURCE_TOOL["inputSchema"]
                        for tool in meta.get("dynamic_tools", [])))
    except (OSError, ValueError, TypeError):
        return False


def _explicit_overrides(config, include_effort=True):
    """Return only non-empty overrides supported by the target RPC contract."""
    keys = ("model", "effort") if include_effort else ("model",)
    return {key: config[key] for key in keys
            if config.get(key) not in (None, "")}


def _actual_model(response):
    """Read the model selected by app-server without selecting one locally."""
    model = response.get("model") if isinstance(response, dict) else None
    thread = response.get("thread") if isinstance(response, dict) else None
    if not isinstance(model, str) or not model:
        model = thread.get("model") if isinstance(thread, dict) else None
    return model if isinstance(model, str) and model else None


def _note_image_blocks(document):
    """Find current-note image evidence without selecting an output format."""
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        return []
    pending, images = list(reversed(document["blocks"])), []
    while pending:
        block = pending.pop()
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if not isinstance(kind, str):
            continue
        if kind in NOTE_IMAGE_TYPES:
            images.append(block)
        if kind in ("details", "quote") and isinstance(block.get("blocks"), list):
            pending.extend(reversed(block["blocks"]))
    return images


def _confined_image_path(root, src):
    if not isinstance(src, str) or not src.strip():
        raise ValueError("missing image path")
    relative = Path(src.replace("\\", "/"))
    if relative.is_absolute():
        raise ValueError("image path must be relative to this paper")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("image path is outside this paper")
    if not path.is_file():
        raise ValueError("image asset does not exist")
    return path


def _verify_bitmap_file(path, expected_sha256=None):
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower())
    if media_type is None:
        raise ValueError("SVG/non-bitmap asset is metadata only; no pixel conversion is performed")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the image input byte limit")
    # A bounded read also covers a file growing after stat().
    with path.open("rb") as image_file:
        raw = image_file.read(MAX_IMAGE_BYTES + 1)
    verify_image(raw, media_type)
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("image SHA-256 no longer matches its registered evidence")
    return raw


def _token_estimate(text):
    ascii_count = sum(ord(char) < 128 for char in text)
    return (ascii_count + 2) // 3 + (len(text) - ascii_count) * 2


class AppServer:
    """Line JSON-RPC transport; notifications remain available across RPC races."""
    def __init__(self, binary=None):
        binary = binary or shutil.which("codex")
        if not binary:
            raise RuntimeError("installed codex executable not found; configure codex_bin")
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.write_lock = threading.Lock()
        self.pending, self.notices, self.tool_requests = {}, [], []
        self.token_usage = {}
        self.counter = 0
        self.closed = False
        self.process = subprocess.Popen([binary, "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.reader = threading.Thread(target=self._read, name="codex-stdio", daemon=True)
        self.reader.start()
        try:
            self.rpc("initialize", {"clientInfo": {"name": "academic-reader-bridge", "version": "3.1.0"},
                                    "capabilities": {"experimentalApi": True}}, timeout=30)
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def send(self, message):
        with self.write_lock:
            if self.closed or self.process.poll() is not None:
                raise UnknownDelivery("app-server exited")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "id" in message and "method" in message:
                    if message["method"] == "item/tool/call":
                        with self.changed:
                            self.tool_requests.append(message)
                            self.changed.notify_all()
                        continue
                    # Reader sessions never grant writes/commands or interactive
                    # approvals. These should not occur in read-only + never mode.
                    method = message["method"]
                    if method.endswith("/requestApproval"):
                        self.send({"id": message["id"], "result": {"decision": "decline"}})
                    else:
                        self.send({"id": message["id"], "error": {"code": -32601, "message": "Reader bridge does not support interactive host requests"}})
                    continue
                with self.changed:
                    if "id" in message:
                        self.pending[message["id"]] = message
                    else:
                        if message.get("method") == "thread/tokenUsage/updated":
                            params = message.get("params", {})
                            if params.get("threadId") and isinstance(params.get("tokenUsage"), dict):
                                # Preserve real host counters verbatim, including
                                # cachedInputTokens when the host reports it.
                                self.token_usage[(params["threadId"], params.get("turnId"))] = deepcopy(params["tokenUsage"])
                        self.notices.append(message)
                    self.changed.notify_all()
        finally:
            with self.changed:
                self.closed = True
                self.changed.notify_all()

    def rpc(self, method, params, timeout=60):
        with self.lock:
            self.counter += 1
            rid = self.counter
        self.send({"id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self.changed:
            while rid not in self.pending:
                if self.closed or time.monotonic() >= deadline:
                    raise UnknownDelivery("No confirmed response for " + method)
                self.changed.wait(min(1, deadline - time.monotonic()))
            message = self.pending.pop(rid)
        if "error" in message:
            raise RuntimeError(method + ": " + json.dumps(message["error"], ensure_ascii=False))
        return message["result"]

    def drain_turn(self, thread_id, turn_id):
        with self.lock:
            found, keep = [], []
            for message in self.notices:
                params = message.get("params", {})
                turn = params.get("turn", {})
                if params.get("threadId") == thread_id and (params.get("turnId") == turn_id or turn.get("id") == turn_id):
                    found.append(message)
                else:
                    keep.append(message)
            # Unrelated noise is bounded; current turn notices are consumed above.
            self.notices = keep[-2000:]
            return found

    def drain_tools(self, thread_id, turn_id):
        with self.lock:
            found, keep = [], []
            for message in self.tool_requests:
                params = message.get("params", {})
                (found if params.get("threadId") == thread_id and params.get("turnId") == turn_id else keep).append(message)
            self.tool_requests = keep
            return found

    def respond_tool(self, request_id, result):
        self.send({"id": request_id, "result": result})

    def close(self):
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class CodexAppServerBridge(HostBridge):
    def __init__(self, config):
        self.config = config
        self.server = AppServer(config.get("codex_bin"))
        self.active = set()
        self.resumed = {}
        self.sessions = set()
        self.verified_answer = False
        self.session_models = {}
        self.last_actual_model = None
        self.model_modalities = {}
        self.model_context_windows = {}
        self.session_instruction_tokens = {}
        self.reader_sessions = {}
        self.source_turns = {}
        # This metadata RPC does not submit a model turn or pick a new model.
        try:
            cursor = None
            for _page in range(5):
                response = self.server.rpc("model/list", {"limit": 100, **({"cursor": cursor} if cursor else {})}, timeout=15)
                for model in response.get("data", []):
                    if isinstance(model, dict) and isinstance(model.get("inputModalities"), list):
                        for key in (model.get("id"), model.get("model")):
                            if key:
                                self.model_modalities[key] = model["inputModalities"]
                                for field in ("contextWindow", "contextWindowTokens", "maxInputTokens", "inputTokenLimit"):
                                    if type(model.get(field)) is int and model[field] > 0:
                                        self.model_context_windows[key] = model[field]
                                        break
                cursor = response.get("nextCursor")
                if not cursor:
                    break
        except (RuntimeError, OSError, ValueError):
            pass

    def health(self):
        usage = getattr(self, "last_token_usage", None)
        measured = {"token_usage": deepcopy(usage)} if usage is not None else {}
        if getattr(self, "last_error", None):
            return {"health": "unavailable", "error": self.last_error, "image_input": self.image_capability(), **measured}
        return {"health": "ready" if getattr(self, "verified_answer", False) else "unknown", "image_input": self.image_capability(), **measured}

    def image_capability(self, thread_id=None):
        override = self.config.get("image_input_supported")
        if override is not None:
            return "supported" if override else "unsupported"
        if thread_id is None:
            model = getattr(self, "last_actual_model", None)
        else:
            model = getattr(self, "session_models", {}).get(thread_id)
        modalities = getattr(self, "model_modalities", {}).get(model)
        if not model or modalities is None:
            return "unknown"
        return "supported" if "image" in modalities else "unsupported"

    def recheck(self):
        self.verified_answer = False
        try:
            response = self.server.rpc("model/list", {"limit": 100}, timeout=15)
            if not hasattr(self, "model_modalities"):
                self.model_modalities = {}
            if not hasattr(self, "model_context_windows"):
                self.model_context_windows = {}
            for model in response.get("data", []):
                if isinstance(model, dict) and isinstance(model.get("inputModalities"), list):
                    for key in (model.get("id"), model.get("model")):
                        if key:
                            self.model_modalities[key] = model["inputModalities"]
                            for field in ("contextWindow", "contextWindowTokens", "maxInputTokens", "inputTokenLimit"):
                                if type(model.get(field)) is int and model[field] > 0:
                                    self.model_context_windows[key] = model[field]
                                    break
            self.last_error = None
        except (RuntimeError, OSError) as exc:
            self.last_error = str(exc)
            if getattr(self.server, "closed", False):
                raise UnknownDelivery("Host transport closed; supervisor must reconnect") from exc
        return self.health()

    def session(self, paper_dir, thread_id=None):
        # SQLite owns the per-paper mapping. A loaded host thread is already a
        # live conversation: resuming it for every annotation replaces session
        # configuration and needlessly rebuilds delivery state from history.
        if thread_id and thread_id in getattr(self, "sessions", set()):
            return thread_id
        guide_path = Path(__file__).resolve().parents[2] / "references" / "answer_worker_guide.md"
        instructions = guide_path.read_text(encoding="utf-8-sig")
        params = {"cwd": str(paper_dir), "approvalPolicy": "never", "sandbox": "read-only",
                  "developerInstructions": instructions}
        params.update(_explicit_overrides(self.config, include_effort=False))
        if thread_id:
            response = self.server.rpc("thread/resume", {**params, "threadId": thread_id})
        else:
            params["dynamicTools"] = [deepcopy(SOURCE_TOOL)]
            try:
                response = self.server.rpc("thread/start", params)
            except RuntimeError as exc:
                raise RuntimeError("Host could not register the required read-only reader_source dynamic tool; update the host protocol support: " + str(exc)) from exc
        thread = response["thread"]
        if not hasattr(self, "session_models"):
            self.session_models = {}
        actual_model = _actual_model(response)
        self.session_models[thread["id"]] = actual_model
        self.last_actual_model = actual_model
        if not hasattr(self, "session_instruction_tokens"):
            self.session_instruction_tokens = {}
        self.session_instruction_tokens[thread["id"]] = _token_estimate(instructions)
        if not hasattr(self, "reader_sessions"):
            self.reader_sessions = {}
        self._restore_delivery(thread, fresh=not thread_id)
        self.resumed[thread["id"]] = thread
        self.sessions.add(thread["id"])
        return thread["id"]

    def _restore_delivery(self, thread, fresh=False):
        previous = self.reader_sessions.get(thread["id"], {})
        if not thread.get("path") and previous.get("host_history_path"):
            thread = {**thread, "path": previous["host_history_path"]}
        packets = _rollout_packets(thread) if not fresh else None
        compacted = any(item.get("type") in ("contextCompaction", "compaction")
            for turn in thread.get("turns", []) for item in turn.get("items", []))
        complete = packets is not None or not (thread.get("turnsBackwardsCursor") or compacted or
            any(turn.get("itemsBackwardsCursor") for turn in thread.get("turns", [])))
        state = _delivered_state(packets if packets is not None else _thread_packets(thread))
        if not fresh and not complete:
            state["baseline_status"] = "unverified"
            state["body_sha256"], state["blocks"] = None, {}
        state["source_tool"] = fresh or previous.get("source_tool", False) or _resumed_source_tool(thread)
        if thread.get("path"):
            state["host_history_path"] = thread["path"]
        self.reader_sessions[thread["id"]] = state

    def _question_packet(self, thread_id, task, context):
        if not hasattr(self, "reader_sessions"):
            self.reader_sessions = {}
        state = self.reader_sessions.setdefault(thread_id, {"blocks": {}, "seen_tasks": set(),
                                                            "body_sha256": None, "source_tool": False})
        wire = deepcopy(context)
        if "reader_body" not in wire and any(isinstance(wire.get(view), dict) for view in ("overview", "explanation")):
            wire["reader_body"] = reader_body(wire)
        for retired in ("overview", "explanation", "sections", "learning_catalog", "previous_answer_content",
                        "answer_content", "study_state", "source_texts"):
            wire.pop(retired, None)
        if isinstance(wire.get("original_pdf"), dict):
            wire["original_pdf"] = {key: value for key, value in wire["original_pdf"].items() if key != "text_pages"}
        if isinstance(wire.get("selected_blocks"), list):
            wire["selected_blocks"] = [readable_block(block) for block in wire["selected_blocks"] if isinstance(block, dict)]
        proposed = None
        if isinstance(wire.get("reader_body"), list):
            if state.get("baseline_status") == "unverified":
                raise ConfirmedFailure("Existing host history does not establish the delivered reader body; reconnect with readable complete host history or explicitly rebuild the paper session")
            digest = json_digest(wire["reader_body"])
            blocks = {_body_key(block): json_digest(block) for block in wire["reader_body"]}
            mode = "snapshot" if state["body_sha256"] is None else "delta"
            if mode == "delta":
                wire["reader_body"] = [block for block in wire["reader_body"]
                                       if state["blocks"].get(_body_key(block)) != blocks[_body_key(block)]]
            wire["reader_delivery"] = {"protocol": 1, "mode": mode, "body_sha256": digest,
                "base_body_sha256": state["body_sha256"],
                "removed_blocks": sorted(set(state["blocks"]) - set(blocks)),
                "source_tool": "available" if state["source_tool"] else "unavailable in this legacy/unverified host thread; qualify missing original evidence"}
            if mode == "delta" and list(state["blocks"]) != list(blocks):
                wire["reader_delivery"]["block_order"] = list(blocks)
            proposed = {"blocks": blocks, "body_sha256": digest, "baseline_status": "verified"}
            # The initial build measures the complete readable body. Recompute
            # after delta selection so capacity describes this turn's input.
            wire.pop("context_capacity", None)
        shared = {key: json_digest(wire[key]) for key in SHARED_CONTEXT_FIELDS if key in wire}
        prior_shared = state.get("context_fingerprints", {})
        removed = sorted(set(prior_shared) - set(shared))
        if removed and "reader_delivery" in wire:
            wire["reader_delivery"]["removed_context_fields"] = removed
        for key, digest in shared.items():
            if prior_shared.get(key) == digest:
                wire.pop(key, None)
        proposed = {**(proposed or {}), "context_fingerprints": shared}
        if isinstance(context.get("original_pdf"), dict):
            proposed["original_pdf"] = context["original_pdf"]
        return {"task": _compact_task(task["payload"], state["seen_tasks"]), "context": wire}, proposed

    def _clear_active(self, thread_id):
        # Resume may reveal an earlier unknown active turn. An explicit replacement
        # must interrupt that turn before turn/start (which could otherwise steer).
        thread = self.resumed.get(thread_id, {})
        for turn in thread.get("turns", []):
            if turn.get("status") == "inProgress":
                self.interrupt(thread_id, turn["id"])
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    notices = self.server.drain_turn(thread_id, turn["id"])
                    if any(n.get("method") == "turn/completed" for n in notices):
                        break
                    time.sleep(0.1)
                else:
                    raise UnknownDelivery("Previous host turn has not confirmed interruption")
        self.resumed.pop(thread_id, None)

    def start(self, thread_id, task, context):
        self._clear_active(thread_id)
        packet, proposed = self._question_packet(thread_id, task, context)
        note_images = _note_image_blocks(task.get("payload", {}).get("note_content"))
        # Input type never changes the output contract; each turn owns its copy.
        output_schema = deepcopy(ANSWER_SCHEMA)
        prompt = (TASK_PREFIX + task["id"] + "\n"
                  + PACKET_PREFIX + json.dumps(packet, ensure_ascii=False)
                  + "\nAnswer in Chinese unless requested otherwise; follow the persistent session answer/sources contract.")
        params = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
                  "clientUserMessageId": task["id"], "outputSchema": output_schema}
        params.update(_explicit_overrides(self.config))
        attachments = task.get("payload", {}).get("attachments", [])
        source_images = context.get("selected_source_images", [])
        if attachments or note_images or source_images:
            if self.image_capability(thread_id) != "supported":
                params["input"][0]["text"] += "\nThis adapter/model has not verified image input support. You have NOT seen any image pixels in this turn, including figure/diagram references. Use only their supplied structure, alt and caption metadata; explicitly state that image pixels were not supplied."
            else:
                papers_root = (Path(self.config["vault"]).resolve() / "papers").resolve()
                root = (papers_root / task["paper_id"]).resolve()
                if root.parent != papers_root:
                    raise ConfirmedFailure("Paper directory is outside the configured vault")
                supplied, skipped, inspected = {}, list(context.get("source_image_metadata_only", [])), {}
                for attachment in attachments:
                    try:
                        path = _confined_image_path(root, attachment.get("src"))
                        if path not in supplied:
                            _verify_bitmap_file(path, attachment.get("sha256"))
                            supplied[path] = attachment["src"]
                    except (OSError, ValueError) as exc:
                        raise ConfirmedFailure("Invalid attached image: " + str(exc)) from exc
                for block in note_images:
                    src = block.get("src", block.get("asset"))
                    path = None
                    try:
                        path = _confined_image_path(root, src)
                        if path in supplied:
                            continue
                        if path in inspected:
                            reason = inspected[path]
                            if reason:
                                skipped.append({"src": src, "reason": reason})
                            continue
                        _verify_bitmap_file(path)
                        inspected[path] = None
                        supplied[path] = src
                    except (OSError, ValueError) as exc:
                        reason = str(exc)
                        skipped.append({"src": src, "reason": reason})
                        if path is not None:
                            inspected[path] = reason
                for image in source_images:
                    try:
                        path = _confined_image_path(root, image.get("src"))
                        if path in supplied:
                            continue
                        if not isinstance(image.get("sha256"), str):
                            raise ValueError("selected source image has no verified hash")
                        _verify_bitmap_file(path, image["sha256"])
                        supplied[path] = image["src"]
                    except (OSError, ValueError) as exc:
                        if "SVG/non-bitmap" in str(exc):
                            skipped.append({"src": image.get("src"), "reason": str(exc)})
                        else:
                            raise ConfirmedFailure("Invalid selected source image: " + str(exc)) from exc
                for path in supplied:
                    params["input"].append({"type": "localImage", "path": str(path)})
                params["input"][0]["text"] += "\nImage pixel delivery for this turn: " + json.dumps({"pixel_input_paths": list(supplied.values()), "metadata_only_references": skipped}, ensure_ascii=False)
                params["input"][0]["text"] += "\nOnly pixel_input_paths above were supplied as localImage input. You have NOT seen the pixels of metadata_only_references or any other images mentioned in paper context/history/catalog. Use only supplied structure, alt/caption metadata for those images and explicitly state that their pixels were not supplied. Distinguish observed image content from paper claims."
            if note_images:
                params["input"][0]["text"] += "\nCurrent note figure/diagram paths remain registered source-asset metadata, not uploaded attachments. Do not invent attachment IDs or sha256 values for them; preserve their existing structure and captions."
        # Check the complete, final source packet before turn/start. No clipping
        # or automatic subset fallback may masquerade as complete evidence.
        text_input = params["input"][0]["text"]
        byte_count = len(text_input.encode("utf-8"))
        if byte_count > MAX_TURN_SOURCE_BYTES:
            raise ConfirmedFailure("Complete question and evidence exceed the 1.5MB turn-source limit; nothing was truncated or submitted")
        model = getattr(self, "session_models", {}).get(thread_id)
        window = self.config.get("context_token_limit") or getattr(self, "model_context_windows", {}).get(model)
        estimate = _token_estimate(text_input) + getattr(self, "session_instruction_tokens", {}).get(thread_id, 0)
        # Page/image tokens vary by host. Reserve a conservative 4096 per image
        # plus response room; this is an estimate, not tokenizer certification.
        estimate += 4096 * (len(params["input"]) - 1) + 8192
        if type(window) is int and estimate > window:
            raise ConfirmedFailure("Estimated complete turn context exceeds the verified/configured model window; nothing was truncated or submitted")
        params["input"][0]["text"] += "\nSource packet capacity: " + json.dumps({"utf8_bytes": byte_count,
            "byte_limit": MAX_TURN_SOURCE_BYTES, "estimated_tokens_including_instruction_image_answer_reserves": estimate,
            "model_context_window": window if type(window) is int else None,
            "capacity_verification": "estimated against host/configured window" if type(window) is int else "host window unknown; byte bound checked only",
            "previous_host_turns": "host-managed history; not included in this new-turn token estimate", "truncated": False})
        if len(params["input"][0]["text"].encode("utf-8")) > MAX_TURN_SOURCE_BYTES:
            raise ConfirmedFailure("Complete source packet including capacity metadata exceeds 1.5MB; nothing was submitted")
        pdf_evidence = context.get("original_pdf", {})
        if pdf_evidence.get("status") == "complete":
            papers_root = (Path(self.config["vault"]).resolve() / "papers").resolve()
            root = (papers_root / task["paper_id"]).resolve()
            if root.parent != papers_root:
                raise ConfirmedFailure("Paper directory is outside the configured vault")
            try:
                if digest_file(confined(root, "source.pdf")) != pdf_evidence.get("pdf_sha256"):
                    raise ValueError("source.pdf changed after context preparation")
            except (OSError, ValueError) as exc:
                raise ConfirmedFailure(str(exc)) from exc
        try:
            response = self.server.rpc("turn/start", params)
        except UnknownDelivery:
            raise
        except RuntimeError as exc:
            # An explicit RPC error confirms turn/start was rejected.
            raise ConfirmedFailure(str(exc)) from exc
        turn_id = response["turn"]["id"]
        self.active.add((thread_id, turn_id))
        state = self.reader_sessions[thread_id]
        if proposed is not None:
            state.update(proposed)
        state["seen_tasks"].add(task["id"])
        state["seen_tasks"].update(_exchange_key(item) for item in packet["task"].get("history", []))
        if not hasattr(self, "source_turns"):
            self.source_turns = {}
        if pdf_evidence.get("pdf_sha256"):
            self.source_turns[(thread_id, turn_id)] = {"paper_id": task["paper_id"],
                "pdf_sha256": pdf_evidence["pdf_sha256"], "requests": 0}
        return turn_id

    def _source_requests(self, thread_id, turn_id):
        drain = getattr(self.server, "drain_tools", None)
        if drain is None:
            return
        for message in drain(thread_id, turn_id):
            result = {"success": False, "contentItems": []}
            try:
                params = message.get("params", {})
                state = getattr(self, "source_turns", {}).get((thread_id, turn_id))
                if params.get("tool") != SOURCE_TOOL["name"] or params.get("namespace") not in (None, ""):
                    raise ValueError("only the registered reader_source tool is permitted")
                if not state:
                    raise ValueError("no current hash-bound source context for this turn")
                state["requests"] += 1
                if state["requests"] > 3:
                    raise ValueError("three source requests per question exhausted; explain remaining evidence gaps")
                arguments = params.get("arguments")
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                papers_root = (Path(self.config["vault"]).resolve() / "papers").resolve()
                root = (papers_root / state["paper_id"]).resolve()
                if root.parent != papers_root:
                    raise ValueError("source request is outside the configured paper vault")
                evidence = requested_pdf_evidence(root, arguments, state["pdf_sha256"])
                images = evidence.pop("images", [])
                image_items, delivered = [], []
                if images and self.image_capability(thread_id) == "supported":
                    image_bytes = 0
                    for image in images:
                        path = _confined_image_path(root, image["src"])
                        raw = _verify_bitmap_file(path, image["sha256"])
                        image_bytes += len(raw)
                        if image_bytes > 8 * 1024 * 1024:
                            raise ValueError("requested page pixels exceed the 8 MiB tool-image limit")
                        image_items.append({"type": "inputImage", "imageUrl": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")})
                        delivered.append({"page": image["page"], "sha256": image["sha256"]})
                evidence["pixel_delivery"] = delivered
                evidence["image_input_note"] = ("Only pixel_delivery pages were supplied as actual pixels. Other pages have text only; extraction can lose mathematics/layout.")
                result = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(evidence, ensure_ascii=False)}, *image_items]}
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                result["contentItems"] = [{"type": "inputText", "text": json.dumps({"error": str(exc),
                    "evidence_supplied": False, "instruction": "Do not infer the missing source; refine the bounded request or state the evidence gap."}, ensure_ascii=False)}]
            self.server.respond_tool(message["id"], result)

    @staticmethod
    def _answer(items, allow_legacy_rich=False):
        messages = [item for item in items if item.get("type") == "agentMessage" and item.get("text")]
        final = [item for item in messages if item.get("phase") in ("final_answer", "finalAnswer")]
        if not (final or messages):
            raise ConfirmedFailure("Completed host turn contains no final agentMessage")
        raw = (final or messages)[-1]["text"].strip()
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw[7:-3].strip()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ConfirmedFailure("Host final answer is not valid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("answer"), str) or not isinstance(data.get("sources"), list):
            raise ConfirmedFailure("Host final answer failed structured answer/sources contract")
        if not allow_legacy_rich and set(data) != {"answer", "sources"}:
            raise ConfirmedFailure("Host final answer must contain exactly answer and sources")
        if not allow_legacy_rich:
            # The service owns Markdown safety, citation validation and CAS.
            return {"answer": data["answer"], "sources": data["sources"]}
        # Recovery can encounter a delivered pre-3.9 answer. Decode it without
        # asking new turns to generate that retired format or changing storage.
        result = {key: data[key] for key in ("answer", "sources", "answer_content", "attachments", "study_state") if key in data and data[key] is not None}
        for key in ("answer_content", "attachments", "study_state"):
            encoded = data.get(key + "_json")
            if encoded is None:
                continue
            if not isinstance(encoded, str) or len(encoded) > 1000000:
                raise ConfirmedFailure("Invalid " + key + " wire JSON string")
            try:
                value = json.loads(encoded)
            except ValueError as exc:
                raise ConfirmedFailure("Invalid " + key + " wire JSON") from exc
            if not isinstance(value, list if key == "attachments" else dict):
                raise ConfirmedFailure("Wrong " + key + " wire JSON shape")
            result[key] = value
        return result

    def wait(self, thread_id, turn_id, pulse, allow_legacy_rich=False):
        items = []
        last_pulse = 0
        try:
            while True:
                if time.monotonic() - last_pulse >= 4:
                    last_pulse = time.monotonic()
                    if pulse():
                        self.interrupt(thread_id, turn_id)
                self._source_requests(thread_id, turn_id)
                for notice in self.server.drain_turn(thread_id, turn_id):
                    params = notice.get("params", {})
                    if notice.get("method") == "item/completed":
                        items.append(params.get("item", {}))
                    if notice.get("method") == "turn/completed":
                        turn = params["turn"]
                        if turn.get("status") != "completed":
                            raise ConfirmedFailure("Host turn " + str(turn.get("status")) + ": " + json.dumps(turn.get("error"), ensure_ascii=False))
                        final_items = turn.get("items", [])
                        if not any(i.get("type") == "agentMessage" for i in final_items + items):
                            history = self.server.rpc("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
                            final_items = next((t.get("items", []) for t in history.get("turns", []) if t["id"] == turn_id), [])
                        answer = self._answer(final_items + items, allow_legacy_rich=allow_legacy_rich)
                        usage = getattr(self.server, "token_usage", {}).get((thread_id, turn_id))
                        self.last_token_usage = ({"thread_id": thread_id, "turn_id": turn_id,
                            "source": "thread/tokenUsage/updated", "tokenUsage": deepcopy(usage)} if usage is not None else None)
                        self.verified_answer = True
                        self.last_error = None
                        return answer
                if self.server.closed:
                    raise UnknownDelivery("Host closed before terminal turn confirmation")
                with self.server.changed:
                    self.server.changed.wait(0.5)
        finally:
            self.active.discard((thread_id, turn_id))
            getattr(self, "source_turns", {}).pop((thread_id, turn_id), None)

    def recover(self, task, pulse):
        thread_id = task.get("thread_id")
        if not thread_id:
            raise UnknownDelivery("No durable host thread id; delivery cannot be reconciled")
        try:
            self.session(self.config["vault"] + "/papers/" + task["paper_id"], thread_id)
            thread = self.server.rpc("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
            self._restore_delivery(thread)
        except (RuntimeError, OSError, KeyError) as exc:
            # Authentication, missing rollout and read errors do not establish
            # whether an acknowledged turn executed. Keep the paper blocked.
            raise UnknownDelivery("Host history unavailable; explicit retry or rebuild may be needed: " + str(exc)) from exc
        turn = next((t for t in thread.get("turns", []) if t["id"] == task.get("turn_id")), None)
        if turn is None:
            # A crash after turn/start but before its response can still be found
            # by our stable clientUserMessageId / exact task marker in the rollout.
            for candidate in thread.get("turns", []):
                for item in candidate.get("items", []):
                    if item.get("type") == "userMessage" and (item.get("clientId") == task["id"] or
                        any("Reader task identity (do not alter): " + task["id"] in v.get("text", "") for v in item.get("content", []) if isinstance(v, dict))):
                        turn = candidate
                        break
                if turn:
                    break
        if turn is None:
            raise UnknownDelivery("Delivered turn not found in host history; explicit retry required")
        if turn["status"] == "completed":
            self.verified_answer = True
            self.last_error = None
            return self._answer(turn.get("items", []), allow_legacy_rich=True)
        if turn["status"] in ("failed", "interrupted"):
            raise ConfirmedFailure("Recovered host turn " + turn["status"])
        if turn["status"] == "inProgress":
            if not hasattr(self, "source_turns"):
                self.source_turns = {}
            for item in turn.get("items", []):
                if item.get("type") == "userMessage":
                    for content in item.get("content", []):
                        packet = _history_packet(content.get("text")) if isinstance(content, dict) else None
                        digest = (packet.get("context", {}).get("original_pdf", {}).get("pdf_sha256") or
                                  self.reader_sessions.get(thread_id, {}).get("original_pdf", {}).get("pdf_sha256")) if packet else None
                        if digest:
                            self.source_turns[(thread_id, turn["id"])] = {"paper_id": task["paper_id"],
                                "pdf_sha256": digest, "requests": sum(i.get("type") == "dynamicToolCall" and i.get("tool") == SOURCE_TOOL["name"] for i in turn.get("items", []))}
            self.active.add((thread_id, turn["id"]))
            return self.wait(thread_id, turn["id"], pulse, allow_legacy_rich=True)
        raise UnknownDelivery("Host turn status cannot be reconciled")

    def interrupt(self, thread_id, turn_id):
        if not thread_id or not turn_id:
            return
        self.server.rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=15)

    def close(self):
        for thread_id in getattr(self, "sessions", set()):
            try:
                thread = self.server.rpc("thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=3)["thread"]
                for turn in thread.get("turns", []):
                    if turn.get("status") == "inProgress":
                        self.active.add((thread_id, turn["id"]))
            except (RuntimeError, OSError):
                pass
        for thread_id, turn_id in list(self.active):
            try:
                self.interrupt(thread_id, turn_id)
            except (RuntimeError, OSError):
                pass
        self.server.close()
