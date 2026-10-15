"""Synthetic v3.1 persistence, recovery, service locking and image capability tests."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zlib

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from reader_store import ReaderStore, StoreError
from serve_reader_v3 import ReaderServer
from runtime_lock import RuntimeLock
from annotation_assets import save_upload, verify_image, MAX_IMAGE_BYTES
from bridge_client import build_context
from content_contract import validate_rich_document
from bridges.codex_app_server import CodexAppServerBridge, ANSWER_SCHEMA
from bridges.base import UnknownDelivery, ConfirmedFailure
from tests.test_reader_v3 import fixture


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff")) + chunk(b"IEND", b"")


def rich(text):
    return {"version": 1, "blocks": [{"id": "text-1", "type": "paragraph", "text": text}]}


class StoreV31Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.directory = fixture(self.vault)
        self.now = [1000.0]
        self.store = ReaderStore(self.vault, lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, **body):
        return self.store.create("paper_a", {"user_note": "question", "block_id": "body-1", **body})

    def active(self):
        self.store.register_bridge("b1", "mock")
        task = self.store.claim("b1")["task"]
        self.store.acknowledge(task["id"], "b1", task["claim_token"], "thread-a", "turn-a")
        return task

    def test_pause_is_durable_and_ordinary_ensure_cannot_resume(self):
        self.create()
        self.store.assistant_intent("pause", None, "pause-1", "mock")
        self.store.close()
        self.store = ReaderStore(self.vault, lambda: self.now[0])
        self.store.assistant_intent("ensure", "paper_a", "ensure-1", "mock")
        self.store.register_bridge("b1", "mock")
        self.assertIsNone(self.store.claim("b1")["task"])
        self.assertEqual("paused", self.store.status("paper_a")["assistant"]["state"])
        self.store.assistant_intent("ensure", "paper_a", "enable-1", "mock", enable=True)
        self.assertIsNotNone(self.store.claim("b1")["task"])

    def test_pause_allows_current_answer_and_blocks_next(self):
        self.create()
        task = self.active()
        self.create()
        self.store.assistant_intent("pause", None, "pause-1", "mock")
        self.assertEqual([], self.store.heartbeat("b1", [{"task_id": task["id"], "claim_token": task["claim_token"]}])["controls"])
        self.assertTrue(self.store.result(task["id"], "b1", task["claim_token"], {"answer": "done", "sources": []})["applied"])
        self.assertIsNone(self.store.claim("b1")["task"])

    def test_rebuild_request_idempotence_and_old_generation_block(self):
        anno = self.create()["annotation"]
        task = self.active()
        self.create()
        self.store.mark_unknown(task["id"], "b1", task["claim_token"], "cannot read history")
        one = self.store.assistant_intent("rebuild", "paper_a", "rebuild-1", "mock")
        two = self.store.assistant_intent("rebuild", "paper_a", "rebuild-1", "mock")
        self.assertEqual(one["session_generation"], two["session_generation"])
        self.assertTrue(two["duplicate"])
        reply = self.store.result(task["id"], "b1", task["claim_token"], {"answer": "late old reply", "sources": []})
        self.assertTrue(reply["stale_generation"])
        self.assertEqual("unknown", self.store._task(task["id"])["state"])
        self.assertIsNone(self.store.claim("b1")["task"])
        self.assertEqual("", self.store.get("paper_a", anno["id"])["answer"])
        self.assertEqual([], self.store.recoverable("b1")["tasks"])
        self.store.edit("paper_a", anno["id"], {"revision": 1}, "cancel")
        self.assertIsNotNone(self.store.claim("b1")["task"])

    def test_ensure_inspection_revision_idempotence_and_pause(self):
        self.store.assistant_intent("ensure", "paper_a", "ensure-1", "mock")
        revision = self.store.settings()["connection_revision"]
        self.store.assistant_intent("ensure", "paper_a", "ensure-1", "mock")
        self.assertEqual(revision, self.store.settings()["connection_revision"])
        self.store.assistant_intent("pause", None, "pause-1", "mock")
        self.store.assistant_intent("ensure", "paper_a", "ensure-2", "mock")
        self.assertEqual(revision, self.store.settings()["connection_revision"])
        self.store.assistant_intent("ensure", "paper_a", "enable-1", "mock", enable=True)
        self.assertEqual(revision + 1, self.store.settings()["connection_revision"])

    def test_ack_mapping_cas_cannot_replace_session_thread(self):
        self.create()
        task = self.active()
        with self.assertRaises(StoreError):
            self.store.acknowledge(task["id"], "b1", task["claim_token"], "different-thread")
        self.assertEqual("thread-a", self.store.db.execute("SELECT thread_id FROM sessions").fetchone()[0])

    def test_health_unknown_heartbeat_does_not_claim_model_ready(self):
        self.store.register_bridge("b1", "codex", {"health": "unknown"})
        self.assertEqual("connected", self.store.status("paper_a")["assistant"]["state"])
        self.store.heartbeat("b1", info={"health": "ready"})
        self.assertEqual("ready", self.store.status("paper_a")["assistant"]["state"])
        self.store.heartbeat("b1", info={"health": "unavailable", "error": "login failed"})
        self.assertEqual("error", self.store.status("paper_a")["assistant"]["state"])

    def test_rich_question_answer_history_queue_restart_and_projection(self):
        attachment = save_upload(self.directory, {"data_base64": base64.b64encode(png()).decode(), "media_type": "image/png", "caption": "input figure"})
        state = {"component-1": {"step": 2}}
        anno = self.create(user_note="ignored legacy text", note_content=rich("rich question"), attachments=[attachment], study_state=state)["annotation"]
        task = self.active()
        self.assertEqual("rich question", task["payload"]["user_note"])
        self.assertEqual(state, task["payload"]["study_state"])
        answer = rich("rich answer")
        self.store.result(task["id"], "b1", task["claim_token"], {"answer": "ignored legacy answer", "answer_content": answer, "sources": [], "attachments": [attachment]})
        saved = self.store.get("paper_a", anno["id"])
        self.assertEqual("rich answer", saved["answer"])
        self.assertEqual(answer, saved["history"][0]["answer_content"])
        self.assertEqual(1, len(saved["attachments"]))
        follow = self.store.edit("paper_a", anno["id"], {"revision": 1, "note_content": rich("followup"), "attachments": [attachment], "study_state": state}, "followup")
        self.assertEqual("rich answer", follow["annotation"]["history"][0]["answer"])
        self.assertEqual("followup", follow["task"]["payload"]["user_note"])
        self.store.close()
        self.store = ReaderStore(self.vault)
        self.assertEqual(rich("followup"), self.store.get("paper_a", anno["id"])["note_content"])
        projection = json.loads((self.directory / "annotations.json").read_text(encoding="utf-8"))
        self.assertEqual(answer, projection["annotations"][0]["history"][0]["answer_content"])

    def test_rich_validation_rejects_script_missing_asset_and_duplicate_id(self):
        for document in (rich("<script>alert(1)</script>"), {"version": 1, "blocks": [{"id": "f", "type": "figure", "src": "../secret.png"}]}, {"version": 1, "blocks": rich("x")["blocks"] * 2}):
            with self.assertRaises(StoreError):
                self.create(note_content=document)

    def test_plain_edit_clears_stale_rich_note_but_retains_history(self):
        anno = self.create(note_content=rich("original"))["annotation"]
        changed = self.store.edit("paper_a", anno["id"], {"revision": 1, "user_note": "plain", "submit": True})
        self.assertNotIn("note_content", changed["annotation"])
        self.assertEqual("plain", changed["task"]["payload"]["user_note"])
        self.assertEqual(rich("original"), changed["annotation"]["history"][0]["note_content"])

    def test_managed_draft_cas_survives_restart(self):
        first = self.store.save_draft("paper_a", {"id": "draft-1", "data": {"note": "unfinished", "note_content": rich("draft")}})["draft"]
        with self.assertRaises(StoreError):
            self.store.save_draft("paper_a", {"id": "draft-1", "data": {"note": "stale"}})
        self.store.close()
        self.store = ReaderStore(self.vault)
        self.assertEqual(first, self.store.list_drafts("paper_a")["drafts"][0])
        self.store.delete_draft("paper_a", "draft-1", first["revision"])
        self.assertEqual([], self.store.list_drafts("paper_a")["drafts"])

    def test_asset_verification_bounds_paths_and_deduplication(self):
        raw = png()
        one = save_upload(self.directory, {"data_base64": base64.b64encode(raw).decode(), "media_type": "image/png", "name": "../../outside.png"})
        two = save_upload(self.directory, {"data_base64": base64.b64encode(raw).decode(), "media_type": "image/png"})
        self.assertEqual(one, two)
        self.assertTrue(one["src"].startswith("assets/annotations/"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), one["sha256"])
        for content, kind in ((b"<svg onload='evil'/>", "image/png"), (raw[:-1], "image/png"), (raw, "image/jpeg"), (b"x" * (MAX_IMAGE_BYTES + 1), "image/png")):
            with self.assertRaises(ValueError):
                verify_image(content, kind)
        with self.assertRaises(StoreError):
            self.store.validate_attachments("paper_a", [{**one, "src": "../outside.png"}])
        with self.assertRaises(StoreError):
            self.store.validate_attachments("paper_a", [{**one, "sha256": "0" * 64}])

    def test_canonical_catalog_selection_extracted_from_parent_block(self):
        from deploy_reader import load_paper_data
        data = load_paper_data(self.directory / "paper-data.js")
        data["learning_catalog"] = {"concepts": [{"id": "c1", "plain_definition": "definition"}], "aids": [{"id": "a1", "config": {"title": "walkthrough"}}]}
        data["overview"]["sections"][0]["blocks"][0]["learning"] = {"concept_ids": ["c1"], "aid_ids": ["a1"]}
        data["overview"]["sections"][0]["blocks"][0]["learning"]["formula_explanations"] = [{"formula_ref": "formula-1"}]
        data["overview"]["sections"][0]["blocks"].append({"id": "formula-1", "type": "formula", "latex": "x=1"})
        data["overview"]["sections"][0]["blocks"].append({"id": "aid-block", "type": "learning_aid", "aid_id": "a1"})
        (self.directory / "paper-data.js").write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        task = {"payload": {"anchor": {"view": "overview", "content_id": "overview-1", "field": "learning.concept_ids.0.plain_definition"}}}
        self.assertEqual("definition", build_context(self.directory, task)["selected_fields"][0]["value"])
        task["payload"]["anchor"]["field"] = "learning.aid_ids.0.config.title"
        self.assertEqual("walkthrough", build_context(self.directory, task)["selected_fields"][0]["value"])
        task["payload"]["anchor"]["field"] = "learning.formula_explanations.0.latex"
        self.assertEqual("x=1", build_context(self.directory, task)["selected_fields"][0]["value"])
        task["payload"]["anchor"].update(content_id="aid-block", field="aid_id.config.title")
        self.assertEqual("walkthrough", build_context(self.directory, task)["selected_fields"][0]["value"])

    def test_preloaded_notes_never_enter_automatic_question_context(self):
        from deploy_reader import load_paper_data
        data = load_paper_data(self.directory / "paper-data.js")
        data["default_annotations"] = [
            {"id":"selected", "view":"overview", "content_id":"overview-1", "content":{"version":1,"blocks":[{"id":"p","type":"paragraph","text_zh":"预先解释"}]}},
            {"id":"other", "view":"explanation", "content_id":"other-block", "content":{}}]
        (self.directory / "paper-data.js").write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        saved_question=rich("用户早先基于说明主动创建的追问")
        task = {"payload":{"anchor":{"view":"overview","content_id":"overview-1","field":"text_zh"},
            "note_content":saved_question, "history":[{"note_content":rich("旧引用语境"),"answer_content":rich("旧回答")} ]}}
        original=json.loads(json.dumps(task))
        context = build_context(self.directory, task)
        self.assertNotIn("default_annotations", context)
        self.assertNotIn("default_annotations", context["evidence_contract"])
        self.assertEqual(task, original)
        self.assertTrue(context["selected_fields"])
        data["default_annotations"]={"retired":"unknown shape"}
        (self.directory / "paper-data.js").write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        self.assertNotIn("default_annotations", build_context(self.directory, task))

    def test_moved_learning_block_resolves_old_and_new_anchor_paths(self):
        from deploy_reader import load_paper_data
        data = load_paper_data(self.directory / "paper-data.js")
        data["learning_catalog"] = {"aids": [{"id":"a1", "config":{"title":"same teaching graph"}}]}
        roots = data["overview"]["sections"][0]["blocks"]
        roots[0]["learning"] = {"blocks":[{"id":"moved-aid", "type":"learning_aid", "aid_id":"a1"}]}
        path = self.directory / "paper-data.js"
        path.write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        for block_id, field in (("moved-aid", "aid_id.config.title"), ("overview-1", "learning.blocks.0.aid_id.config.title")):
            task = {"payload":{"anchor":{"view":"overview","content_id":block_id,"field":field}}}
            self.assertEqual("same teaching graph", build_context(self.directory, task)["selected_fields"][0]["value"])
        roots.append({"id":"second-parent", "type":"paragraph", "learning":{"blocks":[{"id":"moved-aid", "type":"learning_aid", "aid_id":"a1"}]}})
        path.write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        task["payload"]["anchor"].update(content_id="moved-aid", field="aid_id.config.title")
        self.assertEqual([], build_context(self.directory, task)["selected_fields"])
        roots.append({"id":"moved-aid", "type":"paragraph", "text_zh":"top-level identity wins"})
        path.write_text("window.PAPER_DATA=" + json.dumps(data) + ";", encoding="utf-8")
        task["payload"]["anchor"]["field"] = "text_zh"
        self.assertEqual("top-level identity wins", build_context(self.directory, task)["selected_fields"][0]["value"])


class AdapterV31Tests(unittest.TestCase):
    def test_simple_wire_schema_is_strict_and_legacy_recovery_remains_compatible(self):
        def check(schema):
            if schema.get("type") == "object":
                self.assertIs(False, schema["additionalProperties"])
                self.assertEqual(set(schema["properties"]), set(schema["required"]))
                for property_schema in schema["properties"].values():
                    check(property_schema)
            if schema.get("type") == "array":
                check(schema["items"])
        check(ANSWER_SCHEMA)
        self.assertEqual({"answer", "sources"}, set(ANSWER_SCHEMA["properties"]))
        wire = {"answer": "fallback", "sources": [], "answer_content_json": json.dumps(rich("content")), "attachments_json": "[]", "study_state_json": '{"component":{"step":2}}'}
        normalized = CodexAppServerBridge._answer([{"type": "agentMessage", "text": json.dumps(wire)}], allow_legacy_rich=True)
        self.assertEqual(rich("content"), normalized["answer_content"])
        self.assertEqual({"component": {"step": 2}}, normalized["study_state"])
        self.assertEqual([], normalized["attachments"])
        wire["answer_content_json"] = "broken JSON"
        with self.assertRaises(ConfirmedFailure):
            CodexAppServerBridge._answer([{"type": "agentMessage", "text": json.dumps(wire)}], allow_legacy_rich=True)

    def test_model_catalog_metadata_does_not_select_or_override_host_model(self):
        class RPC:
            def __init__(self):
                self.calls = []
            def rpc(self, method, params, timeout=60):
                self.calls.append((method, params))
                if method == "model/list":
                    return {"data": [{"id": "default", "isDefault": True, "inputModalities": ["text"]}, {"id": "vision", "inputModalities": ["text", "image"]}]}
                return {"thread": {"id": "thread-a"}, "model": "vision"}
        server = RPC()
        with patch("bridges.codex_app_server.AppServer", return_value=server):
            adapter = CodexAppServerBridge({})
        adapter.session(Path("fixture"))
        self.assertEqual("supported", adapter.image_capability("thread-a"))
        self.assertNotIn("model", server.calls[-1][1])
        self.assertNotIn("effort", server.calls[-1][1])

    def test_default_catalog_vision_model_is_not_used_without_actual_thread_model(self):
        class RPC:
            def __init__(self):
                self.calls = []
            def rpc(self, method, params, timeout=60):
                self.calls.append((method, params))
                if method == "model/list":
                    return {"data": [{"id": "vision", "isDefault": True,
                                       "inputModalities": ["text", "image"]}]}
                if method == "thread/start":
                    return {"thread": {"id": "thread-a"}}
                raise AssertionError(method)
        server = RPC()
        with patch("bridges.codex_app_server.AppServer", return_value=server):
            adapter = CodexAppServerBridge({})
        adapter.session(Path("fixture"))
        self.assertEqual("unknown", adapter.image_capability("thread-a"))
        self.assertNotIn("model", server.calls[-1][1])
        self.assertNotIn("effort", server.calls[-1][1])

    def test_bridge_restore_generic_error_remains_unknown_without_resend(self):
        from bridge_client import run
        calls = []
        candidate = {"id": "task-a", "paper_id": "paper_a", "claim_token": "nonce-a"}
        class API:
            def __init__(self, config):
                pass
            def post(self, action, data=None, timeout=35):
                calls.append((action, data))
                if action == "recover":
                    return {"tasks": [candidate]}
                if action == "adopt":
                    return {"task": candidate}
                if action == "claim":
                    return {"stop": True}
                return {}
        with patch("bridge_client.ReaderAPI", API), patch("bridges.mock.MockBridge.recover", side_effect=RuntimeError("temporary authentication/read error")):
            run({"adapter": "mock"})
        self.assertEqual(1, sum(action == "unknown" for action, _data in calls))
        self.assertEqual(0, sum(action == "result" for action, _data in calls))

    def adapter(self, modalities=None, actual_model=None):
        class RPC:
            def __init__(self):
                self.calls = []
            def rpc(self, method, params, timeout=60):
                self.calls.append((method, params))
                if method == "thread/start":
                    return {"thread": {"id": "thread-a"}, "model": actual_model}
                if method == "turn/start":
                    return {"turn": {"id": "turn-a"}}
                raise RuntimeError("history temporarily unreadable")
        adapter = CodexAppServerBridge.__new__(CodexAppServerBridge)
        adapter.config, adapter.active, adapter.resumed, adapter.sessions = {"vault": "."}, set(), {}, set()
        adapter.server, adapter.model_modalities, adapter.session_models = RPC(), modalities or {}, {}
        return adapter

    def start_params(self, payload, context=None, adapter=None):
        adapter = adapter or self.adapter()
        adapter.start("thread-a", {"id": "task-a", "paper_id": "paper_a", "payload": payload}, context or {})
        self.assertEqual("turn/start", adapter.server.calls[-1][0])
        return adapter.server.calls[-1][1]

    def test_all_note_and_selected_block_types_keep_simple_output(self):
        blocks = [
            {"id": "f", "type": "formula", "latex": "x=1"},
            {"id": "t", "type": "table", "rows": [["x", 1]]},
            {"id": "fig", "type": "figure", "src": "assets/figure.png"},
            {"id": "d", "type": "diagram", "src": "assets/diagram.svg"},
            {"id": "algo", "type": "algorithm", "lines": ["initialize x"]},
            {"id": "c", "type": "code", "code": "x = 1"},
            {"id": "a", "type": "learning_aid", "aid_id": "aid-1"},
        ]
        for block in blocks:
            for input_kind in ("note", "selected"):
                with self.subTest(kind=block["type"], input_kind=input_kind):
                    payload = {"note_content": {"version": 1, "blocks": [block]}} if input_kind == "note" else {}
                    context = {"selected_blocks": [block]} if input_kind == "selected" else {}
                    params = self.start_params(payload, context)
                    self.assertEqual(ANSWER_SCHEMA, params["outputSchema"])
                    self.assertNotIn("model", params)
                    self.assertNotIn("effort", params)
                    prompt = params["input"][0]["text"]
                    self.assertIn("persistent session answer/sources contract", prompt)
                    self.assertNotIn("their type never requires a rich answer", prompt)
                    self.assertNotIn("Minimal legal learning_aid wire-field fragment", prompt)
                    self.assertNotIn("MUST NOT be null", prompt)

    def test_nested_notes_and_per_turn_schemas_do_not_pollute_global(self):
        baseline = json.loads(json.dumps(ANSWER_SCHEMA))
        note = {"version": 1, "blocks": [{"id": "detail", "type": "details", "summary": "details", "blocks": [
            {"id": "quote", "type": "quote", "blocks": [{"id": "f", "type": "formula", "latex": "x=1"}]}
        ]}]}
        adapter = self.adapter()
        first = self.start_params({"note_content": note}, adapter=adapter)["outputSchema"]
        second = self.start_params({"note_content": rich("ordinary text")}, adapter=adapter)["outputSchema"]
        self.assertEqual(baseline, first)
        self.assertEqual(baseline, second)
        self.assertIsNot(first, second)
        self.assertIsNot(second, ANSWER_SCHEMA)
        first["properties"]["sources"]["items"]["properties"]["label"]["type"] = "null"
        self.assertEqual("string", second["properties"]["sources"]["items"]["properties"]["label"]["type"])
        self.assertEqual(baseline, ANSWER_SCHEMA)

    def test_old_rich_history_is_preserved_in_storage_input_but_not_reinjected(self):
        old_rich = {"version": 1, "blocks": [{"id": "old", "type": "learning_aid", "aid_id": "aid-1"}]}
        payload = {"user_note": "Why does the next step follow?", "history": [{"note_content": old_rich, "answer_content": old_rich}],
                   "study_state": {"aid-1": {"step": 2}}}
        context = {"learning_catalog": {"aids": [{"id": "aid-1"}]}, "previous_answer_content": old_rich}
        original = json.loads(json.dumps({"task": payload, "context": context}))
        params = self.start_params(payload, context)
        prompt = params["input"][0]["text"]
        packet = json.loads(prompt.split("Paper context and question are JSON source data:\n", 1)[1].split("\nAnswer in Chinese", 1)[0])
        self.assertEqual({"task": payload, "context": context}, original)
        self.assertEqual({"user_note": payload["user_note"]}, packet["task"])
        self.assertEqual({}, packet["context"])
        self.assertEqual(ANSWER_SCHEMA, params["outputSchema"])
        self.assertIn("persistent session answer/sources contract", prompt)
        self.assertNotIn("Historical rich answers, figures and component state", prompt)
        self.assertNotIn("Include the relevant supplied registered components as learning_aid blocks", prompt)

    def test_explicit_demonstration_request_is_routed_without_generation(self):
        note = {"version": 1, "blocks": [{"id": "a", "type": "learning_aid", "aid_id": "selected-aid"}]}
        params = self.start_params({"user_note": "请做演示", "note_content": note}, {"learning_catalog": {"aids": [{"id": "selected-aid"}]}})
        prompt = params["input"][0]["text"]
        self.assertEqual(ANSWER_SCHEMA, params["outputSchema"])
        self.assertIn("persistent session answer/sources contract", prompt)
        self.assertNotIn("separate authoring task", prompt)
        self.assertNotIn("Minimal legal learning_aid wire-field fragment", prompt)

    def test_resume_rpc_error_stays_unknown(self):
        adapter = self.adapter()
        with self.assertRaises(UnknownDelivery):
            adapter.recover({"thread_id": "old-thread", "turn_id": "old-turn", "paper_id": "paper_a", "id": "task-a"}, lambda: False)
        self.assertEqual("thread/resume", adapter.server.calls[0][0])
        self.assertNotIn("turn/start", [method for method, _params in adapter.server.calls])

    def test_actual_model_modalities_and_unknown_catalog_metadata(self):
        for model, modalities, expected in (("vision", {"vision": ["text", "image"]}, "supported"), ("text", {"text": ["text"]}, "unsupported"), (None, {"default": ["text", "image"]}, "unknown")):
            adapter = self.adapter(modalities, model)
            adapter.session(Path("fixture"))
            self.assertEqual(expected, adapter.image_capability("thread-a"))
            self.assertNotIn("model", adapter.server.calls[0][1])
            self.assertNotIn("effort", adapter.server.calls[0][1])

    def test_supported_sends_local_image_and_unknown_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = fixture(temporary)
            image = save_upload(directory, {"media_type": "image/png", "data_base64": base64.b64encode(png()).decode()})
            for capability in (True, None, False):
                adapter = self.adapter({"vision": ["text", "image"]} if capability else {})
                adapter.config["vault"] = temporary
                adapter.session_models["thread-a"] = "vision" if capability else None
                adapter.start("thread-a", {"id": "task-a", "paper_id": "paper_a", "payload": {"attachments": [image]}}, {})
                inputs = adapter.server.calls[-1][1]["input"]
                if capability:
                    self.assertEqual("localImage", inputs[1]["type"])
                else:
                    self.assertEqual(1, len(inputs))
                    self.assertIn("have NOT seen", inputs[0]["text"])

    def image_params(self, temporary, blocks, attachments=None, context=None):
        adapter = self.adapter({"vision": ["text", "image"]})
        adapter.config["vault"] = temporary
        adapter.session_models["thread-a"] = "vision"
        payload = {"note_content": {"version": 1, "blocks": blocks}, "attachments": attachments or []}
        return self.start_params(payload, context, adapter)

    def pixel_delivery(self, params):
        prefix = "Image pixel delivery for this turn: "
        return json.loads(next(line[len(prefix):] for line in params["input"][0]["text"].splitlines() if line.startswith(prefix)))

    def test_current_nested_note_bitmap_is_verified_and_sent_without_attachment_fabrication(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = fixture(temporary)
            image = directory / "assets" / "source.PNG"
            image.parent.mkdir(exist_ok=True)
            image.write_bytes(png())
            note = [{"id": "q", "type": "quote", "blocks": [{"id": "d", "type": "details", "summary": "details", "blocks": [
                {"id": "fig", "type": "figure", "asset": "assets/source.PNG", "caption": "source caption"}
            ]}]}]
            context = {"selected_blocks": [{"type": "figure", "src": "assets/other.png"}],
                       "learning_catalog": {"aids": [{"fallback": {"blocks": [{"type": "figure", "src": "assets/catalog.png"}]}}]}}
            with patch("bridges.codex_app_server.verify_image", wraps=verify_image) as verified:
                params = self.image_params(temporary, note, context=context)
            verified.assert_called_once_with(png(), "image/png")
            self.assertEqual([{"type": "localImage", "path": str(image.resolve())}], params["input"][1:])
            self.assertEqual({"pixel_input_paths": ["assets/source.PNG"], "metadata_only_references": []}, self.pixel_delivery(params))
            self.assertIn("not uploaded attachments", params["input"][0]["text"])
            self.assertIn("Do not invent attachment IDs or sha256", params["input"][0]["text"])
            self.assertNotIn('"sha256"', params["input"][0]["text"])
            self.assertNotIn("Attachments are supplied as image input", params["input"][0]["text"])

    def test_unknown_or_unsupported_note_images_are_metadata_without_file_reads(self):
        for capability in (None, False):
            with self.subTest(capability=capability):
                adapter = self.adapter()
                if capability is not None:
                    adapter.config["image_input_supported"] = capability
                payload = {"note_content": {"version": 1, "blocks": [{"id": "fig", "type": "figure", "src": "assets/source.png", "caption": "caption only"}]}}
                with patch("bridges.codex_app_server._confined_image_path") as resolve_image, patch("bridges.codex_app_server.verify_image") as verified:
                    params = self.start_params(payload, adapter=adapter)
                resolve_image.assert_not_called()
                verified.assert_not_called()
                self.assertEqual(1, len(params["input"]))
                self.assertIn("have NOT seen any image pixels", params["input"][0]["text"])
                self.assertIn("caption only", params["input"][0]["text"])

    def test_outside_and_missing_note_assets_are_metadata_only_without_pixel_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture(temporary)
            outside = Path(temporary) / "papers" / "outside.png"
            outside.write_bytes(png())
            blocks = [{"id": "outside", "type": "figure", "src": "../outside.png"},
                      {"id": "missing", "type": "diagram", "src": "assets/missing.png"}]
            with patch("bridges.codex_app_server.verify_image") as verified:
                params = self.image_params(temporary, blocks)
            verified.assert_not_called()
            self.assertEqual(1, len(params["input"]))
            report = self.pixel_delivery(params)
            self.assertEqual([], report["pixel_input_paths"])
            self.assertEqual(["../outside.png", "assets/missing.png"], [ref["src"] for ref in report["metadata_only_references"]])
            self.assertIn("outside this paper", report["metadata_only_references"][0]["reason"])
            self.assertIn("does not exist", report["metadata_only_references"][1]["reason"])

    def test_svg_note_asset_remains_structure_and_caption_without_read_or_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = fixture(temporary)
            image = directory / "assets" / "diagram.svg"
            image.parent.mkdir(exist_ok=True)
            image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>diagram</text></svg>', encoding="utf-8")
            blocks = [{"id": "svg", "type": "diagram", "src": "assets/diagram.svg", "caption": "registered SVG diagram"}]
            with patch("bridges.codex_app_server.verify_image") as verified, patch.object(Path, "open", side_effect=AssertionError("SVG bytes must not be read")):
                params = self.image_params(temporary, blocks)
            verified.assert_not_called()
            self.assertEqual(1, len(params["input"]))
            self.assertIn("no pixel conversion", self.pixel_delivery(params)["metadata_only_references"][0]["reason"])
            self.assertIn("registered SVG diagram", params["input"][0]["text"])
            self.assertIn("their pixels were not supplied", params["input"][0]["text"])

    def test_duplicate_current_note_and_attachment_paths_supply_pixels_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = fixture(temporary)
            attachment = save_upload(directory, {"media_type": "image/png", "data_base64": base64.b64encode(png()).decode()})
            image = (directory / attachment["src"]).resolve()
            blocks = [{"id": "f", "type": "figure", "src": attachment["src"]},
                      {"id": "d", "type": "diagram", "asset": "./" + attachment["src"]}]
            with patch("bridges.codex_app_server.verify_image", wraps=verify_image) as verified:
                params = self.image_params(temporary, blocks, [attachment, attachment])
            verified.assert_called_once()
            self.assertEqual([{"type": "localImage", "path": str(image)}], params["input"][1:])
            self.assertEqual([attachment["src"]], self.pixel_delivery(params)["pixel_input_paths"])
            self.assertEqual(attachment["sha256"], json.loads(params["input"][0]["text"].split("Paper context and question are JSON source data:\n", 1)[1].split("\nAnswer in Chinese", 1)[0])["task"]["attachments"][0]["sha256"])

    def test_oversized_or_invalid_note_bitmaps_are_not_supplied(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = fixture(temporary)
            assets = directory / "assets"
            assets.mkdir(exist_ok=True)
            (assets / "oversized.png").write_bytes(b"x" * (MAX_IMAGE_BYTES + 1))
            (assets / "invalid.png").write_bytes(b"not an image")
            blocks = [{"id": "big", "type": "figure", "src": "assets/oversized.png"},
                      {"id": "invalid", "type": "figure", "src": "assets/invalid.png"}]
            with patch("bridges.codex_app_server.verify_image", wraps=verify_image) as verified:
                params = self.image_params(temporary, blocks)
            verified.assert_called_once_with(b"not an image", "image/png")
            self.assertEqual(1, len(params["input"]))
            report = self.pixel_delivery(params)
            self.assertEqual([], report["pixel_input_paths"])
            self.assertEqual(2, len(report["metadata_only_references"]))
            self.assertIn("byte limit", report["metadata_only_references"][0]["reason"])


class HTTPV31Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fixture(self.temp.name)
        self.server = ReaderServer(self.temp.name, "paper_a", bridge="none")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.cleanup()
        self.temp.cleanup()

    def request(self, path, body=None, token=True):
        headers = {"X-Reader-Token": self.server.token} if token else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.server.base_url + path, headers=headers, data=json.dumps(body).encode() if body is not None else None)
        with urlopen(request, timeout=4) as response:
            return json.load(response)

    def test_session_identity_401_and_origin_guards(self):
        session = self.request("/api/session?paper_id=paper_a", token=False)
        self.assertEqual(self.server.service_instance_id, session["service_instance_id"])
        with self.assertRaises(HTTPError) as raised:
            self.request("/api/status?paper_id=paper_a", token=False)
        self.assertEqual(401, raised.exception.code)
        self.assertEqual("waiting", self.request("/api/status?paper_id=paper_a")["assistant"]["state"])

    def test_none_service_parallel_ensure_single_child_and_pause(self):
        class Child:
            def poll(self):
                return None
        with patch("serve_reader_v3.subprocess.Popen", return_value=Child()) as launch:
            outputs = []
            def ensure(index):
                outputs.append(self.server.assistant_action("ensure", {"paper_id": "paper_a", "request_id": "ensure-" + str(index)}))
            threads = [threading.Thread(target=ensure, args=(i,)) for i in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertEqual(10, len(outputs))
            self.assertEqual(1, launch.call_count)
            self.assertEqual("codex", self.server.bridge_kind)
            self.request("/api/assistant/pause", {"request_id": "pause-http"})
            self.assertEqual("paused", self.request("/api/assistant/ensure", {"paper_id": "paper_a", "request_id": "ensure-paused"})["assistant"]["state"])
            self.server.bridge_process = None

    def test_upload_and_drafts_endpoints(self):
        saved = self.request("/api/assets", {"paper_id": "paper_a", "media_type": "image/png", "name": "image.png", "data_base64": base64.b64encode(png()).decode()})
        self.assertEqual("image/png", saved["attachment"]["media_type"])
        draft = self.request("/api/papers/paper_a/drafts", {"id": "draft-a", "data": {"question": "unfinished"}})["draft"]
        self.assertEqual(draft, self.request("/api/papers/paper_a/drafts")["drafts"][0])

    def test_service_lifetime_lock_prevents_second_server(self):
        with self.assertRaises(RuntimeError):
            ReaderServer(self.temp.name, "paper_a", bridge="none")

    def test_launcher_reuse_calls_ensure_instead_of_skipping_bridge(self):
        import argparse
        import open_reader
        state = {"url": self.server.base_url, "token": self.server.token}
        args = argparse.Namespace(bridge="codex", no_open=True)
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def read(self):
                return b'{"assistant":{"state":"connecting"}}'
        with patch("open_reader.live_v3_state", return_value=state), patch("open_reader.LOCAL_HTTP.open", return_value=Response()) as request, patch("open_reader.write_bat"):
            self.assertEqual(0, open_reader.open_v3(Path(self.temp.name) / "papers" / "paper_a", Path(self.temp.name), args))
        self.assertTrue(request.call_args[0][0].full_url.endswith("/api/assistant/ensure"))

    def test_launcher_reuse_rejects_explicit_overrides_without_ensure_or_launch(self):
        import argparse
        import open_reader
        state = {"url": self.server.base_url, "token": self.server.token}
        args = argparse.Namespace(bridge="codex", no_open=True, model="vision", effort="high")
        with patch("open_reader.live_v3_state", return_value=state), \
                patch("open_reader.LOCAL_HTTP.open") as request, \
                patch("open_reader.subprocess.Popen") as launch, \
                patch("open_reader.write_bat"), patch("builtins.print") as output:
            result = open_reader.open_v3(Path(self.temp.name) / "papers" / "paper_a", Path(self.temp.name), args)
        self.assertNotEqual(0, result)
        request.assert_not_called()
        launch.assert_not_called()
        self.assertTrue(any("先执行 --stop" in str(call) for call in output.call_args_list))

    def test_os_lock_cross_process_ownership(self):
        lock = RuntimeLock(Path(self.temp.name) / ".reader" / "probe.lock", timeout=0).acquire()
        program = "import sys; sys.path.insert(0,sys.argv[1]); from runtime_lock import RuntimeLock; RuntimeLock(sys.argv[2],timeout=0).acquire()"
        try:
            child = subprocess.run([sys.executable, "-c", program, str(SCRIPTS), str(lock.path)], capture_output=True, timeout=10)
            self.assertNotEqual(0, child.returncode)
            self.assertIn(b"already owned", child.stderr)
        finally:
            lock.close()
        child = subprocess.run([sys.executable, "-c", program, str(SCRIPTS), str(lock.path)], capture_output=True, timeout=10)
        self.assertEqual(0, child.returncode, child.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
