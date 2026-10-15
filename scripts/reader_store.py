#!/usr/bin/env python3
"""SQLite authority for reader v3. JSON files are atomic export projections only.

No model SDK is used here. Acknowledged delivery is never silently redelivered:
an expired running lease becomes unknown and requires recovery or explicit retry.
"""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


IDENTITY_PALETTE = ("#497da7", "#8b6daa", "#af8a49", "#528d83", "#a56f84", "#647eaf")


def color_seed(annotation_id):
    """Only annotation identity participates; author/source never does."""
    return int.from_bytes(hashlib.sha256(str(annotation_id).encode("utf-8")).digest()[:8], "big")


def anchor_regions(annotation):
    anchor = annotation.get("anchor") or {}
    if not isinstance(anchor, dict):
        return []
    fragments = anchor.get("fragments") or [anchor]
    regions = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        content_id = fragment.get("content_id") or anchor.get("content_id") or annotation.get("block_id")
        if not content_id:
            continue
        view = fragment.get("view") or anchor.get("view") or "translation"
        view = "translation" if view == "body" else view
        language = fragment.get("language") or anchor.get("language") or "zh"
        field = fragment.get("field") or anchor.get("field") or "text_zh"
        block = anchor.get("kind") == "block" or fragment.get("kind") == "block" or field == "block"
        start, end = fragment.get("start"), fragment.get("end")
        # Unknown legacy offsets conservatively span the field, avoiding an
        # invisible identity collision until an anchor map supplies exact ranges.
        interval = (start, end) if type(start) is int and type(end) is int and start < end else None
        regions.append((view, language, content_id, field, block, interval))
    return regions


def anchors_overlap(one, two):
    for first in anchor_regions(one):
        for second in anchor_regions(two):
            if first[:3] != second[:3]:
                continue
            if first[4] or second[4]:
                return True
            if first[3] != second[3]:
                continue
            if first[5] is None or second[5] is None or max(first[5][0], second[5][0]) < min(first[5][1], second[5][1]):
                return True
    return False


class StoreError(Exception):
    def __init__(self, status, message, current=None):
        super().__init__(message)
        self.status, self.current = status, current


class ReaderStore:
    def __init__(self, vault, clock=time.time):
        self.vault = Path(vault).resolve()
        self.state_dir = self.vault / ".reader"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(str(self.state_dir / "state.sqlite3"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS annotations (
          paper_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
          deleted INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL,
          PRIMARY KEY(paper_id,id));
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, annotation_id TEXT NOT NULL,
          revision INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
          bridge_id TEXT, claim_token TEXT, lease_until REAL, thread_id TEXT,
          turn_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          error TEXT, result TEXT, lease_generation INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(state,created_at);
        CREATE TABLE IF NOT EXISTS requests (
          paper_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
          response TEXT NOT NULL, PRIMARY KEY(paper_id,request_id));
        CREATE TABLE IF NOT EXISTS sessions (
          paper_id TEXT NOT NULL, adapter TEXT NOT NULL, thread_id TEXT NOT NULL,
          updated_at TEXT NOT NULL, PRIMARY KEY(paper_id,adapter));
        CREATE TABLE IF NOT EXISTS bridges (
          id TEXT PRIMARY KEY, adapter TEXT NOT NULL, seen_at REAL NOT NULL, info TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT, kind TEXT NOT NULL,
          body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assistant_settings (
          id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
          adapter TEXT NOT NULL DEFAULT 'codex', updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts (
          paper_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
          body TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(paper_id,id));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}
        if "lease_generation" not in columns:
            self.db.execute("ALTER TABLE tasks ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0")
        if "session_generation" not in columns:
            self.db.execute("ALTER TABLE tasks ADD COLUMN session_generation INTEGER NOT NULL DEFAULT 0")
        session_columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)")}
        if "generation" not in session_columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
        if "state" not in session_columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN state TEXT NOT NULL DEFAULT 'unknown'")
        setting_columns = {row[1] for row in self.db.execute("PRAGMA table_info(assistant_settings)")}
        if "connection_revision" not in setting_columns:
            self.db.execute("ALTER TABLE assistant_settings ADD COLUMN connection_revision INTEGER NOT NULL DEFAULT 0")
        self.db.execute("INSERT OR IGNORE INTO assistant_settings(id,paused,adapter,updated_at) VALUES(1,0,'codex',?)", (now_iso(),))
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def paper_dir(self, paper):
        if not isinstance(paper, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", paper):
            raise StoreError(400, "invalid paper id")
        root = (self.vault / "papers").resolve()
        target = (root / paper).resolve()
        if target.parent != root or not target.is_dir():
            raise StoreError(404, "paper not found")
        return target

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self.changed.notify_all()

    def _event(self, paper, kind, body):
        cur = self.db.execute("INSERT INTO events(paper_id,kind,body,created_at) VALUES(?,?,?,?)",
                              (paper, kind, json_text(body), now_iso()))
        return cur.lastrowid

    def _get(self, paper, aid, include_deleted=False):
        row = self.db.execute("SELECT * FROM annotations WHERE paper_id=? AND id=?", (paper, aid)).fetchone()
        if not row or (row["deleted"] and not include_deleted):
            raise StoreError(404, "annotation not found")
        return json.loads(row["body"])

    def get(self, paper, aid):
        self.paper_dir(paper)
        with self.lock:
            return self._get(paper, aid)

    def _save(self, anno):
        if not anno.get("identity_color"):
            anno["identity_color"] = self._allocate_color(anno)
        self.db.execute("INSERT INTO annotations(paper_id,id,revision,deleted,body) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(paper_id,id) DO UPDATE SET revision=excluded.revision, "
                        "deleted=excluded.deleted,body=excluded.body",
                        (anno["paper_id"], anno["id"], anno["revision"], int(anno.get("deleted", False)), json_text(anno)))
        self._event(anno["paper_id"], "annotation", {"paper_id": anno["paper_id"], "annotation": anno})

    def _allocate_color(self, anno, preferred=None):
        rows = self.db.execute("SELECT body FROM annotations WHERE paper_id=? AND deleted=0 AND id<>? ORDER BY rowid",
                               (anno["paper_id"], anno["id"])).fetchall()
        neighbors = [json.loads(row[0]) for row in rows]
        used = {other.get("identity_color") for other in neighbors if anchors_overlap(anno, other)}
        if preferred is not None:
            if not isinstance(preferred, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}|hsl\([\d., %]+\)", preferred) or len(preferred) > 80:
                raise StoreError(400, "invalid identity_color")
            if preferred not in used:
                return preferred
        seed = color_seed(anno["id"])
        offset = seed % len(IDENTITY_PALETTE)
        for step in range(len(IDENTITY_PALETTE)):
            candidate = IDENTITY_PALETTE[(offset + step) % len(IDENTITY_PALETTE)]
            if candidate not in used:
                return candidate
        step = 0
        while True:
            hue = ((seed % 360000) / 1000 + step * 137.508) % 360
            candidate = "hsl(%.3f 65%% 42%%)" % hue
            if candidate not in used:
                return candidate
            step += 1

    def _cas(self, anno, revision):
        if type(revision) is not int or revision != anno["revision"]:
            raise StoreError(409, "revision conflict; reload before editing", anno)

    def _request(self, paper, request_id, body):
        if not request_id:
            return None
        if not isinstance(request_id, str) or len(request_id) > 200:
            raise StoreError(400, "invalid client_request_id")
        fingerprint = hashlib.sha256(json_text(body).encode()).hexdigest()
        row = self.db.execute("SELECT * FROM requests WHERE paper_id=? AND request_id=?", (paper, request_id)).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise StoreError(409, "client_request_id was reused with different content")
            return json.loads(row["response"])
        return None

    def _remember(self, paper, request_id, body, response):
        if request_id:
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?)", (paper, request_id,
                hashlib.sha256(json_text(body).encode()).hexdigest(), json_text(response)))

    def _queue(self, anno, purpose="question"):
        task_id = "task_" + hashlib.sha256((anno["paper_id"] + "\0" + anno["id"] + "\0" + str(anno["revision"])).encode()).hexdigest()[:32]
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            return dict(row)
        if not anno.get("user_note", "").strip():
            raise StoreError(400, "user_note is required for a question")
        stamp = now_iso()
        payload = {"paper_id": anno["paper_id"], "annotation_id": anno["id"], "revision": anno["revision"],
                   "user_note": anno["user_note"], "selected_text": anno.get("selected_text", ""),
                   "anchor": anno.get("anchor", {}), "block_id": anno.get("block_id", ""),
                   "history": anno.get("history", [])[-24:], "purpose": purpose,
                   "request_id": anno.get("request_id") or task_id}
        for key in ("note_content", "attachments", "study_state"):
            if key in anno:
                payload[key] = anno[key]
        self.db.execute("INSERT INTO tasks(id,paper_id,annotation_id,revision,state,payload,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", (task_id, anno["paper_id"], anno["id"], anno["revision"], "queued", json_text(payload), stamp, stamp))
        anno["status"] = "queued"
        anno["task_id"] = task_id
        anno["error"] = ""
        anno.setdefault("history", []).append({"task_id": task_id, "revision": anno["revision"],
            "user_note": anno["user_note"], "answer": "", "sources": [], "status": "queued", "created_at": stamp})
        for key in ("note_content", "attachments", "study_state"):
            if key in anno:
                anno["history"][-1][key] = json.loads(json_text(anno[key]))
        return self._task(task_id)

    def _task(self, tid):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            raise StoreError(404, "task not found")
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        return task

    def create(self, paper, body):
        self.paper_dir(paper)
        with self.transaction():
            request_body = {"action": "create", **body}
            old = self._request(paper, body.get("client_request_id"), request_body)
            if old:
                return old
            aid = body.get("id") or "anno_" + uuid.uuid4().hex
            if not isinstance(aid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", aid):
                raise StoreError(400, "invalid annotation id")
            row = self.db.execute("SELECT deleted FROM annotations WHERE paper_id=? AND id=?", (paper, aid)).fetchone()
            if row:
                raise StoreError(409, "annotation id already exists (including tombstones)")
            stamp = now_iso()
            anno = {"id": aid, "paper_id": paper, "revision": 1, "deleted": False,
                    "block_id": body.get("block_id") or "", "anchor": body.get("anchor", {}),
                    "selected_text": body.get("selected_text", body.get("quote", "")),
                    "user_note": body.get("user_note", body.get("note", "")),
                    "status": "draft", "unread": False, "answer": "", "sources": [], "history": [],
                    "created_at": stamp, "updated_at": stamp, "request_id": body.get("client_request_id")}
            for key in ("note_content", "attachments", "study_state"):
                if key in body:
                    anno[key] = body[key]
            self._validate_fields(anno)
            anno["identity_color"] = self._allocate_color(anno, body.get("identity_color"))
            task = self._queue(anno) if body.get("submit", True) else None
            self._save(anno)
            response = {"annotation": anno, "task": task}
            self._remember(paper, body.get("client_request_id"), request_body, response)
        self.project(paper)
        return response

    def _validate_fields(self, anno):
        for key, text_key in (("note_content", "user_note"), ("answer_content", "answer")):
            if key in anno and anno[key] is not None:
                from content_contract import validate_rich_document, rich_to_text
                directory = self.paper_dir(anno["paper_id"])
                from deploy_reader import load_paper_data
                path = directory / "paper-data.js"
                catalog = load_paper_data(path).get("learning_catalog") if path.exists() else None
                if len(json_text(anno[key])) > 1000000:
                    raise StoreError(400, "oversized " + key)
                errors, _warnings = validate_rich_document(anno[key], directory, catalog)
                if errors:
                    raise StoreError(400, "invalid " + key + ": " + "; ".join(errors[:5]))
                anno[text_key] = rich_to_text(anno[key])
            elif key in anno:
                anno.pop(key)
        if "attachments" in anno:
            anno["attachments"] = self.validate_attachments(anno["paper_id"], anno["attachments"])
        if "study_state" in anno and (not isinstance(anno["study_state"], dict) or len(json_text(anno["study_state"])) > 200000):
            raise StoreError(400, "invalid study_state")
        for field in ("user_note", "selected_text", "block_id"):
            if not isinstance(anno.get(field), str) or len(anno[field]) > 100000:
                raise StoreError(400, "invalid " + field)
        if not isinstance(anno.get("anchor"), dict) or len(json_text(anno["anchor"])) > 200000:
            raise StoreError(400, "invalid anchor")

    def validate_attachments(self, paper, attachments):
        if not isinstance(attachments, list) or len(attachments) > 16:
            raise StoreError(400, "attachments must be an array of at most 16 images")
        from annotation_assets import verify_image
        root = self.paper_dir(paper)
        output, seen = [], set()
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise StoreError(400, "invalid attachment")
            src = attachment.get("src")
            if not isinstance(src, str) or "\\" in src or ":" in src or src.startswith("/") or any(p.startswith(".") for p in src.split("/")):
                raise StoreError(400, "attachment must use a confined local asset path")
            path = (root / src).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise StoreError(400, "attachment asset missing, outside paper or oversized")
            raw = path.read_bytes()
            try:
                verify_image(raw, attachment.get("media_type"))
            except ValueError as exc:
                raise StoreError(400, str(exc)) from exc
            digest = hashlib.sha256(raw).hexdigest()
            if attachment.get("sha256") not in (None, digest):
                raise StoreError(400, "attachment checksum mismatch")
            for key in ("id", "alt", "caption"):
                if key in attachment and (not isinstance(attachment[key], str) or len(attachment[key]) > 10000):
                    raise StoreError(400, "invalid attachment " + key)
            if src not in seen:
                output.append({**attachment, "id": attachment.get("id") or "asset_" + digest[:24], "sha256": digest})
                seen.add(src)
        return output

    def settings(self):
        with self.lock:
            return dict(self.db.execute("SELECT * FROM assistant_settings WHERE id=1").fetchone())

    def assistant_intent(self, action, paper, request_id, adapter="codex", enable=False):
        if action not in ("ensure", "pause", "rebuild") or adapter not in ("codex", "mock"):
            raise StoreError(400, "unsupported assistant action or adapter")
        if action != "pause":
            self.paper_dir(paper)
        if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
            raise StoreError(400, "request_id is required")
        body = {"action": action, "paper_id": paper, "adapter": adapter, "enable": bool(enable)}
        with self.transaction():
            previous = self._request("@assistant", request_id, body)
            if previous is not None:
                return {**previous, "duplicate": True}
            if action == "pause":
                self.db.execute("UPDATE assistant_settings SET paused=1,updated_at=? WHERE id=1", (now_iso(),))
            else:
                self.db.execute("UPDATE assistant_settings SET adapter=?,paused=CASE WHEN ? THEN 0 ELSE paused END,updated_at=? WHERE id=1",
                                (adapter, int(enable), now_iso()))
                if not self.settings()["paused"]:
                    self.db.execute("UPDATE assistant_settings SET connection_revision=connection_revision+1 WHERE id=1")
            generation = None
            if action == "rebuild":
                session = self.db.execute("SELECT generation FROM sessions WHERE paper_id=? AND adapter=?", (paper, adapter)).fetchone()
                generation = (session[0] if session else 0) + 1
                self.db.execute("INSERT INTO sessions(paper_id,adapter,thread_id,updated_at,generation,state) VALUES(?,?,?, ?,?,'unknown') "
                                "ON CONFLICT(paper_id,adapter) DO UPDATE SET thread_id='',generation=excluded.generation,state='unknown',updated_at=excluded.updated_at",
                                (paper, adapter, "", now_iso(), generation))
                # A rebuild never resolves an unconfirmed delivery. Existing turns
                # remain durable blockers and old-generation replies cannot apply.
                rows = self.db.execute("SELECT * FROM tasks WHERE paper_id=? AND state IN ('claimed','running')", (paper,)).fetchall()
                for row in rows:
                    state = "queued" if row["state"] == "claimed" else "unknown"
                    self.db.execute("UPDATE tasks SET state=?,error=?,updated_at=? WHERE id=?",
                                    (state, "session rebuilt; explicit retry or cancel required" if state == "unknown" else None, now_iso(), row["id"]))
                    if state == "unknown":
                        self._task_annotation(row, "unknown", "会话已重建；原问题结果未知，请明确重试或取消。")
            response = {"ok": True, "session_generation": generation}
            self._remember("@assistant", request_id, body, response)
            self._event(None, "status", {"assistant_action": action, "paper_id": paper})
        if action == "rebuild":
            self.project(paper)
        return response

    def list_drafts(self, paper):
        self.paper_dir(paper)
        with self.lock:
            return {"drafts": [{"id": row["id"], "revision": row["revision"], "data": json.loads(row["body"]), "updated_at": row["updated_at"]}
                              for row in self.db.execute("SELECT * FROM drafts WHERE paper_id=? ORDER BY updated_at", (paper,))]}

    def save_draft(self, paper, body):
        self.paper_dir(paper)
        draft_id, data = body.get("id"), body.get("data")
        if not isinstance(draft_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", draft_id) or not isinstance(data, dict) or len(json_text(data)) > 200000:
            raise StoreError(400, "invalid or oversized draft")
        with self.transaction():
            row = self.db.execute("SELECT * FROM drafts WHERE paper_id=? AND id=?", (paper, draft_id)).fetchone()
            if row and body.get("revision") != row["revision"]:
                raise StoreError(409, "draft revision conflict", {"id": draft_id, "revision": row["revision"], "data": json.loads(row["body"]), "updated_at": row["updated_at"]})
            if not row and body.get("revision") not in (None, 0):
                raise StoreError(409, "draft no longer exists")
            draft = {"id": draft_id, "revision": row["revision"] + 1 if row else 1, "data": data, "updated_at": now_iso()}
            self.db.execute("INSERT INTO drafts VALUES(?,?,?,?,?) ON CONFLICT(paper_id,id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated_at=excluded.updated_at",
                            (paper, draft_id, draft["revision"], json_text(data), draft["updated_at"]))
        return {"draft": draft}

    def delete_draft(self, paper, draft_id, revision):
        self.paper_dir(paper)
        with self.transaction():
            row = self.db.execute("SELECT revision FROM drafts WHERE paper_id=? AND id=?", (paper, draft_id)).fetchone()
            if row and row[0] != revision:
                raise StoreError(409, "draft revision conflict")
            self.db.execute("DELETE FROM drafts WHERE paper_id=? AND id=?", (paper, draft_id))
        return {"ok": True}

    def edit(self, paper, aid, body, action="edit"):
        self.paper_dir(paper)
        if action == "followup" and not body.get("note_content") and (not isinstance(body.get("user_note"), str) or not body["user_note"].strip()):
            raise StoreError(400, "followup user_note is required")
        with self.transaction():
            request_body = {"action": action, "annotation_id": aid, **body}
            old = self._request(paper, body.get("client_request_id"), request_body)
            if old:
                return old
            anno = self._get(paper, aid)
            self._cas(anno, body.get("revision"))
            if action == "read":
                anno["unread"] = False
                task = None
            elif action == "cancel":
                self.db.execute("UPDATE tasks SET state='cancelled',updated_at=? WHERE paper_id=? AND annotation_id=? AND state IN ('queued','claimed','running','unknown')", (now_iso(), paper, aid))
                anno["status"], anno["error"] = "cancelled", ""
                anno["revision"] += 1
                task = None
            else:
                # Any content revision invalidates previous results. Running old turns
                # stay in tasks and block same-paper delivery until settled/expired.
                anno["revision"] += 1
                anno["request_id"] = body.get("client_request_id")
                anno["updated_at"] = now_iso()
                if action != "retry":
                    if "user_note" in body and "note_content" not in body:
                        anno.pop("note_content", None)
                    for key in ("user_note", "selected_text", "anchor", "block_id", "note_content", "attachments", "study_state"):
                        if key in body:
                            anno[key] = body[key]
                self._validate_fields(anno)
                self.db.execute("UPDATE tasks SET state='cancelled',updated_at=? WHERE paper_id=? AND annotation_id=? "
                                "AND state IN ('queued','claimed')", (now_iso(), paper, aid))
                if action == "retry":
                    # A human explicitly requested a new delivery. Old unknown
                    # turns are retired; the adapter checks/interrupts active host
                    # turns before starting the replacement in the same session.
                    self.db.execute("UPDATE tasks SET state='cancelled',updated_at=? WHERE paper_id=? AND annotation_id=? "
                                    "AND state='unknown'", (now_iso(), paper, aid))
                anno["status"] = "draft"
                anno["unread"] = False
                anno["answer"], anno["sources"] = "", []
                anno.pop("answer_latex", None)
                anno.pop("answer_content", None)
                task = self._queue(anno, action) if action in ("followup", "retry") or body.get("submit", False) else None
            self._save(anno)
            response = {"annotation": anno, "task": task}
            self._remember(paper, body.get("client_request_id"), request_body, response)
        self.project(paper)
        return response

    def mark_read(self, paper, aid, body):
        if "revision" not in body:
            body = {**body, "revision": self.get(paper, aid)["revision"]}
        return self.edit(paper, aid, body, "read")

    def delete(self, paper, aid, revision):
        with self.transaction():
            anno = self._get(paper, aid)
            self._cas(anno, revision)
            anno.update(deleted=True, revision=anno["revision"] + 1, updated_at=now_iso(), unread=False)
            self.db.execute("UPDATE tasks SET state='cancelled',updated_at=? WHERE paper_id=? AND annotation_id=? "
                            "AND state IN ('queued','claimed','running','unknown')", (now_iso(), paper, aid))
            self._save(anno)
        self.project(paper)
        return {"ok": True, "annotation": anno}

    def list_annotations(self, paper):
        self.paper_dir(paper)
        with self.lock:
            rows = self.db.execute("SELECT body FROM annotations WHERE paper_id=? AND deleted=0 ORDER BY rowid", (paper,)).fetchall()
            annotations = [json.loads(r[0]) for r in rows]
            available = self._bridge_available()
            if not available:
                for anno in annotations:
                    if anno["status"] == "queued":
                        anno["status"] = "waiting_bridge"
            return {"paper_id": paper, "annotations": annotations, "revision": self.event_seq()}

    def project(self, paper):
        # Keep the lock through replacement: an older snapshot cannot replace a newer one.
        with self.lock:
            path = self.paper_dir(paper) / "annotations.json"
            rows = self.db.execute("SELECT body FROM annotations WHERE paper_id=? AND deleted=0 ORDER BY rowid", (paper,)).fetchall()
            annotations = [json.loads(row[0]) for row in rows]
            for anno in annotations:
                anno["quote"], anno["note"] = anno.get("selected_text", ""), anno.get("user_note", "")
            data = {"schema_version": 3, "paper_id": paper, "updated_at": now_iso(), "annotations": annotations}
            tmp = path.with_name(path.name + ".reader.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)

    def import_legacy(self, paper, anchors=None):
        path = self.paper_dir(paper) / "annotations.json"
        if not path.exists():
            return 0
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data.get("annotations"), list):
            raise StoreError(400, "legacy annotations must be a list")
        if data.get("schema_version", 1) < 3:
            # Preserve original bytes before the first JSON projection replaces it.
            raw = path.read_bytes()
            imports = self.state_dir / "imports"
            imports.mkdir(exist_ok=True)
            backup = imports / (paper + "-" + hashlib.sha256(raw).hexdigest()[:16] + ".json")
            if not backup.exists():
                tmp = backup.with_suffix(".tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, backup)
        count = 0
        with self.transaction():
            for index, old in enumerate(data["annotations"]):
                if not isinstance(old, dict):
                    continue
                aid = old.get("id") or "legacy_" + hashlib.sha256(json_text(old).encode()).hexdigest()[:24]
                existing = self.db.execute("SELECT body,deleted FROM annotations WHERE paper_id=? AND id=?", (paper, aid)).fetchone()
                if existing:
                    current = json.loads(existing["body"])
                    if not existing["deleted"] and not current.get("identity_color"):
                        self._save(current)
                    continue
                stamp = old.get("created_at") or now_iso()
                answer = old.get("answer") or ""
                anno = {"id": aid, "paper_id": paper, "revision": max(1, int(old.get("revision", 1))),
                        "deleted": bool(old.get("deleted", False)), "block_id": old.get("block_id") or "",
                        "anchor": old.get("anchor", {"view": "body", "language": "zh", "content_id": old.get("block_id") or old.get("section_id") or "legacy-unanchored", "legacy": True}),
                        "selected_text": old.get("selected_text", old.get("quote", "")),
                        "user_note": old.get("user_note", old.get("note", "")), "answer": answer,
                        "sources": old.get("sources", []), "status": "answered" if answer else "draft",
                        "unread": bool(old.get("unread", False)), "created_at": stamp,
                        "updated_at": old.get("updated_at") or stamp, "legacy": old.get("legacy", old)}
                if anchors and aid in anchors:
                    # A migration map repairs location before first color
                    # allocation, while `legacy` retains the untouched old object.
                    anno["anchor"] = anchors[aid]
                if old.get("identity_color"):
                    anno["identity_color"] = old["identity_color"]
                if "answered_at" in old:
                    anno["answered_at"] = old["answered_at"]
                if "answer_latex" in old:
                    anno["answer_latex"] = old["answer_latex"]
                for key in ("note_content", "answer_content", "attachments", "study_state"):
                    if key in old:
                        anno[key] = old[key]
                anno["history"] = json.loads(json_text(old["history"])) if old.get("history") else ([{"task_id": "legacy_" + aid, "revision": anno["revision"],
                    "user_note": anno["user_note"], "answer": answer, "sources": anno["sources"],
                    "status": "answered", "created_at": stamp, "answered_at": old.get("answered_at")}] if answer else [])
                if "answer_latex" in old:
                    for turn in anno["history"]:
                        if turn.get("answer") == answer and "answer_latex" not in turn:
                            turn["answer_latex"] = old["answer_latex"]
                for turn in anno["history"]:
                    if turn.get("answer") == answer:
                        for key in ("note_content", "answer_content", "attachments", "study_state"):
                            if key in anno and key not in turn:
                                turn[key] = anno[key]
                self._save(anno)
                count += 1
        self.project(paper)
        return count

    def register_bridge(self, bridge_id, adapter, info=None):
        if not isinstance(bridge_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", bridge_id):
            raise StoreError(400, "invalid bridge id")
        with self.transaction():
            self.db.execute("INSERT INTO bridges VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE "
                            "SET adapter=excluded.adapter,seen_at=excluded.seen_at,info=excluded.info",
                            (bridge_id, adapter, self.clock(), json_text(info or {})))
            self._event(None, "status", {"bridge_id": bridge_id, "connected": True})
        return {"ok": True, "bridge_id": bridge_id, "lease_seconds": 45}

    def _touch_bridge(self, bid):
        cur = self.db.execute("UPDATE bridges SET seen_at=? WHERE id=?", (self.clock(), bid))
        if cur.rowcount != 1:
            raise StoreError(409, "bridge must register first")

    def unregister_bridge(self, bid):
        with self.transaction():
            self.db.execute("UPDATE bridges SET seen_at=0 WHERE id=?", (bid,))
            self._event(None, "status", {"bridge_id": bid, "connected": False})
        return {"ok": True}

    def _bridge_available(self):
        return not self.settings()["paused"] and bool(self.db.execute("SELECT 1 FROM bridges WHERE seen_at>? LIMIT 1", (self.clock() - 60,)).fetchone())

    def expire_leases(self):
        with self.transaction():
            rows = self.db.execute("SELECT * FROM tasks WHERE state IN ('claimed','running') AND lease_until<?", (self.clock(),)).fetchall()
            for row in rows:
                state = "queued" if row["state"] == "claimed" else "unknown"
                anno = self._get(row["paper_id"], row["annotation_id"], True)
                if anno.get("deleted") or anno["revision"] != row["revision"]:
                    state = "stale"
                self.db.execute("UPDATE tasks SET state=?,error=?,updated_at=? WHERE id=?",
                    (state, "delivery outcome unknown; resume session or explicitly retry" if state == "unknown" else None, now_iso(), row["id"]))
                if state == "unknown":
                    self._task_annotation(row, "unknown", "投递结果未知；可恢复原会话，或明确重试、取消。")
        for paper in set(row["paper_id"] for row in rows):
            self.project(paper)
        return len(rows)

    def claim(self, bridge_id, lease_seconds=45):
        self.expire_leases()
        with self.transaction():
            self._touch_bridge(bridge_id)
            if self.settings()["paused"]:
                return {"task": None, "paused": True}
            # A paper can have one host turn at a time, even across bridge processes.
            row = self.db.execute("SELECT t.* FROM tasks t WHERE t.state='queued' AND NOT EXISTS "
                "(SELECT 1 FROM tasks busy WHERE busy.paper_id=t.paper_id AND busy.state IN ('claimed','running','unknown')) "
                "ORDER BY t.rowid LIMIT 1").fetchone()
            if not row:
                return {"task": None}
            nonce = uuid.uuid4().hex
            self.db.execute("UPDATE tasks SET state='claimed',bridge_id=?,claim_token=?,lease_until=?,updated_at=?,lease_generation=lease_generation+1 WHERE id=?",
                (bridge_id, nonce, self.clock() + lease_seconds, now_iso(), row["id"]))
            task = self._task(row["id"])
            bridge = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (bridge_id,)).fetchone()
            self.db.execute("INSERT OR IGNORE INTO sessions(paper_id,adapter,thread_id,updated_at,generation,state) VALUES(?,?,?, ?,0,'unknown')", (task["paper_id"], bridge[0], "", now_iso()))
            sess = self.db.execute("SELECT thread_id,generation FROM sessions WHERE paper_id=? AND adapter=?", (task["paper_id"], bridge[0])).fetchone()
            self.db.execute("UPDATE tasks SET session_generation=? WHERE id=?", (sess[1], task["id"]))
            task["session_generation"] = sess[1]
            task["session_thread_id"] = sess[0] or None
            return {"task": task}

    def _current_session(self, task):
        bridge = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (task["bridge_id"],)).fetchone()
        if not bridge:
            return False
        session = self.db.execute("SELECT generation FROM sessions WHERE paper_id=? AND adapter=?", (task["paper_id"], bridge[0])).fetchone()
        return session is None and task["session_generation"] == 0 or session is not None and session[0] == task["session_generation"]

    def _owned(self, task_id, bridge_id, claim_token):
        task = self._task(task_id)
        if task["bridge_id"] != bridge_id or task["claim_token"] != claim_token:
            raise StoreError(409, "task lease belongs to another claim")
        return task

    def acknowledge(self, tid, bid, nonce, thread_id=None, turn_id=None):
        with self.transaction():
            self._touch_bridge(bid)
            task = self._owned(tid, bid, nonce)
            if not self._current_session(task):
                raise StoreError(409, "session generation changed; explicit resolution required")
            if task["state"] == "claimed" and (task["lease_until"] or 0) < self.clock():
                raise StoreError(409, "claim lease expired before acknowledgement")
            if task["state"] not in ("claimed", "running", "unknown"):
                raise StoreError(409, "task is no longer active")
            self.db.execute("UPDATE tasks SET state='running',thread_id=COALESCE(?,thread_id),turn_id=COALESCE(?,turn_id),lease_until=?,updated_at=? WHERE id=?",
                (thread_id, turn_id, self.clock()+45, now_iso(), tid))
            if thread_id:
                adapter = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (bid,)).fetchone()[0]
                current = self.db.execute("SELECT thread_id,generation FROM sessions WHERE paper_id=? AND adapter=?", (task["paper_id"], adapter)).fetchone()
                if current and current[0] and current[0] != thread_id:
                    raise StoreError(409, "session thread mapping changed")
                self.db.execute("INSERT INTO sessions(paper_id,adapter,thread_id,updated_at,generation,state) VALUES(?,?,?,?,?,'connected') ON CONFLICT(paper_id,adapter) "
                    "DO UPDATE SET thread_id=excluded.thread_id,updated_at=excluded.updated_at,state='connected' WHERE sessions.generation=excluded.generation", (task["paper_id"], adapter, thread_id, now_iso(), task["session_generation"]))
            self._task_annotation(task, "running")
        self.project(task["paper_id"])
        return {"ok": True}

    def heartbeat(self, bid, tasks=(), info=None):
        controls = []
        with self.transaction():
            self._touch_bridge(bid)
            if info is not None:
                if not isinstance(info, dict) or len(json_text(info)) > 20000:
                    raise StoreError(400, "invalid bridge health info")
                old = json.loads(self.db.execute("SELECT info FROM bridges WHERE id=?", (bid,)).fetchone()[0])
                if info.get("health") in ("ready", "unknown") and "error" not in info:
                    old.pop("error", None)
                self.db.execute("UPDATE bridges SET info=? WHERE id=?", (json_text({**old, **info}), bid))
            for item in tasks:
                task = self._owned(item["task_id"], bid, item["claim_token"])
                if task["state"] in ("claimed", "running", "unknown"):
                    self.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (self.clock()+45, task["id"]))
                anno = self._get(task["paper_id"], task["annotation_id"], True)
                if anno.get("deleted") or anno["revision"] != task["revision"] or task["state"] == "cancelled" or not self._current_session(task):
                    controls.append({"task_id": task["id"], "action": "interrupt", "thread_id": task["thread_id"], "turn_id": task["turn_id"]})
        return {"ok": True, "controls": controls}

    def _task_annotation(self, task, status, error="", result=None):
        anno = self._get(task["paper_id"], task["annotation_id"], True)
        if anno.get("deleted") or anno["revision"] != task["revision"]:
            return False
        anno["status"], anno["error"], anno["updated_at"] = status, error, now_iso()
        for turn in anno.get("history", []):
            if turn.get("task_id") == task["id"]:
                turn["status"] = status
                if result:
                    turn.update(answer=result["answer"], sources=result["sources"], answered_at=now_iso())
                    for key in ("answer_content", "study_state"):
                        if key in result:
                            turn[key] = {**turn.get(key, {}), **result[key]} if key == "study_state" else result[key]
                    turn["attachments"] = self._merge_attachments(turn.get("attachments", []), result.get("attachments", []))
                if error:
                    turn["error"] = error
        if result:
            anno.pop("answer_latex", None)
            anno.update(answer=result["answer"], sources=result["sources"], unread=True, answered_at=now_iso())
            for key in ("answer_content", "study_state"):
                if key in result:
                    anno[key] = {**anno.get(key, {}), **result[key]} if key == "study_state" else result[key]
            anno["attachments"] = self._merge_attachments(anno.get("attachments", []), result.get("attachments", []))
        self._save(anno)
        return True

    def result(self, tid, bid, nonce, result=None, error=None):
        if result is not None:
            if not isinstance(result, dict):
                raise StoreError(400, "invalid result")
            result = json.loads(json_text(result))
            with self.lock:
                task_paper = self._task(tid)["paper_id"]
            validation = {**result, "paper_id": task_paper, "user_note": "", "selected_text": "", "block_id": "", "anchor": {}}
            self._validate_fields(validation)
            result["answer"] = validation.get("answer", result.get("answer"))
            if not isinstance(result, dict) or not isinstance(result.get("answer"), str) or not result["answer"].strip() or not isinstance(result.get("sources"), list):
                raise StoreError(400, "result must contain nonempty answer:string and sources:array")
            if len(json_text(result)) > 1000000 or any(not isinstance(s, dict) for s in result["sources"]):
                raise StoreError(400, "invalid sources or oversized result")
            result = {key: result[key] for key in ("answer", "sources", "answer_content", "attachments", "study_state") if key in result and result[key] is not None}
            if "attachments" in result:
                result["attachments"] = validation["attachments"]
            self.validate_sources(task_paper, result["sources"])
        elif not error:
            raise StoreError(400, "result or error required")
        with self.transaction():
            task = self._owned(tid, bid, nonce)
            if not self._current_session(task):
                return {"ok": True, "applied": False, "stale_generation": True}
            if task["state"] in ("completed", "failed", "stale", "cancelled"):
                return {"ok": True, "applied": False, "duplicate": True}
            if result is not None and task["state"] not in ("running", "unknown"):
                raise StoreError(409, "answer requires acknowledged delivery")
            self.db.execute("UPDATE tasks SET state=?,result=?,error=?,updated_at=? WHERE id=?",
                ("completed" if result else "failed", json_text(result) if result else None, str(error or ""), now_iso(), tid))
            if result:
                adapter = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (bid,)).fetchone()[0]
                self.db.execute("UPDATE sessions SET state='ready',updated_at=? WHERE paper_id=? AND adapter=? AND generation=?", (now_iso(), task["paper_id"], adapter, task["session_generation"]))
            applied = self._task_annotation(task, "answered" if result else "failed", str(error or ""), result)
            if not applied:
                self.db.execute("UPDATE tasks SET state='stale' WHERE id=?", (tid,))
        self.project(task["paper_id"])
        return {"ok": True, "applied": applied}

    @staticmethod
    def _merge_attachments(first, second):
        merged = {item.get("src") or item.get("id"): item for item in first}
        merged.update({item.get("src") or item.get("id"): item for item in second})
        return list(merged.values())

    def mark_unknown(self, tid, bid, nonce, error):
        with self.transaction():
            task = self._owned(tid, bid, nonce)
            if task["state"] in ("running", "unknown"):
                self.db.execute("UPDATE tasks SET state='unknown',error=?,updated_at=? WHERE id=?", (str(error), now_iso(), tid))
                self._task_annotation(task, "unknown", str(error))
        self.project(task["paper_id"])
        return {"ok": True}

    def validate_sources(self, paper, sources):
        """Citations may only identify known paper blocks and positive PDF pages.

        This verifies reference shape/identity, not whether an answer is true.
        Claim-to-evidence accuracy remains a model/reviewer responsibility.
        """
        from deploy_reader import load_paper_data
        path = self.paper_dir(paper) / "paper-data.js"
        if not path.exists():
            return
        data = load_paper_data(path)
        blocks = {}
        known_pages = set()
        views = [data.get("sections", [])]
        modern = data.get("content_contract_version") == "3.2"
        def actual_blocks(roots):
            for block in roots:
                yield block
                if modern and block.get("type") in ("details", "quote"):
                    yield from actual_blocks(block.get("blocks", []))
        for name in ("overview", "explanation"):
            view = data.get(name, {})
            if isinstance(view, dict):
                views.append(view.get("sections", []))
        for sections in views:
            for section in sections:
                if type(section.get("page")) is int:
                    known_pages.add(section["page"])
                for ref in section.get("source_refs", []):
                    if isinstance(ref, dict) and type(ref.get("page")) is int:
                        known_pages.add(ref["page"])
                for block in actual_blocks(section.get("blocks", [])):
                    blocks[block.get("id")] = block
                    if type(block.get("page")) is int:
                        known_pages.add(block["page"])
                    for ref in block.get("source_refs", []):
                        if isinstance(ref, dict) and type(ref.get("page")) is int:
                            known_pages.add(ref["page"])
        page_limit = max(known_pages) if known_pages else None
        if any(source.get("page") is not None for source in sources):
            # Deferred readers need not transcribe every original page. The
            # current hash-verified PDF, rather than registered refs, sets bounds.
            from qa_source_evidence import pdf_text_evidence
            try:
                evidence = pdf_text_evidence(self.paper_dir(paper))
            except (OSError, ValueError, RuntimeError) as exc:
                raise StoreError(400, "cannot verify source.pdf citation page bounds: " + str(exc)) from exc
            if evidence.get("status") == "complete":
                page_limit = evidence["page_count"]
        for source in sources:
            content_id = source.get("content_id") or source.get("block_id")
            if content_id and content_id not in blocks:
                raise StoreError(400, "answer cites an unknown content_id")
            page = source.get("page")
            if page is not None and (type(page) is not int or page < 1):
                raise StoreError(400, "citation page must be a positive PDF page")
            if page is not None and page_limit is not None and page > page_limit:
                raise StoreError(400, "citation page exceeds available paper evidence")
            url = source.get("url", "")
            if url and (not isinstance(url, str) or not re.match(r"^https?://", url)):
                raise StoreError(400, "citation URL must be http(s)")

    def recoverable(self, bid):
        with self.lock:
            if self.settings()["paused"]:
                return {"tasks": [], "paused": True}
            bridge = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (bid,)).fetchone()
            if not bridge:
                raise StoreError(409, "bridge must register first")
            rows = self.db.execute("SELECT t.* FROM tasks t JOIN bridges b ON b.id=t.bridge_id "
                                   "WHERE t.state IN ('running','unknown') AND b.adapter=? ORDER BY t.rowid", (bridge[0],)).fetchall()
            return {"tasks": [dict(r) for r in rows if self._current_session(dict(r))]}

    def adopt(self, tid, bid):
        with self.transaction():
            task = self._task(tid)
            if not self._current_session(task):
                raise StoreError(409, "old session generation needs explicit retry or cancel")
            old_bridge = self.db.execute("SELECT adapter,seen_at FROM bridges WHERE id=?", (task["bridge_id"],)).fetchone()
            new_bridge = self.db.execute("SELECT adapter FROM bridges WHERE id=?", (bid,)).fetchone()
            if not old_bridge or not new_bridge or old_bridge[0] != new_bridge[0]:
                raise StoreError(409, "recovery requires the same host adapter")
            if task["state"] not in ("running", "unknown"):
                raise StoreError(409, "task is not recoverable")
            if task["bridge_id"] != bid and old_bridge[1] > self.clock()-60:
                raise StoreError(409, "previous bridge is still alive")
            nonce = uuid.uuid4().hex
            self.db.execute("UPDATE tasks SET bridge_id=?,claim_token=?,lease_until=?,lease_generation=lease_generation+1 WHERE id=?", (bid, nonce, self.clock()+45, tid))
            return {"task": self._task(tid)}

    def event_seq(self):
        with self.lock:
            return self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def events_after(self, seq, paper=None, limit=200):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE seq>? AND (? IS NULL OR paper_id=? OR paper_id IS NULL) ORDER BY seq LIMIT ?",
                                   (seq, paper, paper, limit)).fetchall()
            return [{"seq": row["seq"], "kind": row["kind"], "data": {**json.loads(row["body"]), "revision": row["seq"]}} for row in rows]

    def status(self, paper=None, service_instance_id="", connecting=False):
        if paper:
            self.paper_dir(paper)
        with self.lock:
            rows = self.db.execute("SELECT id,adapter,seen_at,info FROM bridges").fetchall()
            bridges = [{"id": r["id"], "adapter": r["adapter"], "connected": r["seen_at"] > self.clock()-60,
                        "last_seen": r["seen_at"], "info": json.loads(r["info"])} for r in rows]
            counts = {r[0]: r[1] for r in self.db.execute("SELECT state,COUNT(*) FROM tasks GROUP BY state")}
            paper_counts = {r[0]: r[1] for r in self.db.execute("SELECT state,COUNT(*) FROM tasks WHERE (? IS NULL OR paper_id=?) GROUP BY state", (paper, paper))}
            settings = self.settings()
            connected = [b for b in bridges if b["connected"]]
            if service_instance_id:
                connected = [b for b in connected if b["info"].get("service_instance_id") in (None, service_instance_id)]
            health = "unknown"
            message = "答疑未连接"
            state = "connecting" if connecting else "waiting"
            if connected:
                latest = max(connected, key=lambda b: b["last_seen"])
                health = latest["info"].get("health", "unknown")
                if health not in ("ready", "unknown", "unavailable"):
                    health = "unknown"
                state = "ready" if health == "ready" else "error" if health == "unavailable" else "connected"
                message = "答疑可用" if health == "ready" else latest["info"].get("error") or "宿主已连接，答疑尚未验证"
            else:
                failures = [b for b in bridges if b["info"].get("health") == "unavailable" and b["info"].get("service_instance_id") in (None, service_instance_id)]
                if failures:
                    state, health = "error", "unavailable"
                    message = failures[-1]["info"].get("error") or "宿主连接失败，请检查登录与配置"
            if connecting and not connected and state == "connecting":
                message = "正在连接答疑"
            blocked = [{"task_id": r["id"], "annotation_id": r["annotation_id"], "error": r["error"] or "投递结果未知", "session_generation": r["session_generation"]}
                       for r in self.db.execute("SELECT * FROM tasks WHERE state='unknown' AND (? IS NULL OR paper_id=?)", (paper, paper))]
            if blocked:
                state, message = "unknown", "问题结果未知；请恢复原任务，或明确重试、取消。"
            actions = ["ensure", "pause"] + (["rebuild"] if paper else [])
            if settings["paused"]:
                state, message, actions = "paused", "答疑已暂停", ["enable"] + (["rebuild"] if paper else [])
            session = self.db.execute("SELECT generation,state FROM sessions WHERE paper_id=? AND adapter=?", (paper, settings["adapter"])).fetchone() if paper else None
            assistant = {"enabled": not bool(settings["paused"]), "state": state, "message": message,
                         "allowed_actions": actions, "pending_count": sum(paper_counts.get(s, 0) for s in ("queued", "claimed", "running", "unknown")),
                         "service_instance_id": service_instance_id, "health": health,
                         "session_generation": session[0] if session else 0, "session_state": session[1] if session else "unknown",
                         "blocked_tasks": blocked, "pause_scope": "vault"}
            unread = self.db.execute("SELECT body FROM annotations WHERE deleted=0").fetchall()
            return {"api_version": 3, "bridges": bridges, "bridge_available": self._bridge_available(), "counts": counts,
                    "assistant": assistant, "unread_count": sum(bool(json.loads(r[0]).get("unread")) for r in unread), "revision": self.event_seq()}
