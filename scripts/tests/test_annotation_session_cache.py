"""Persistent per-paper conversations; local SQLite and simulated host only."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridges.base import ConfirmedFailure, UnknownDelivery
from bridges.codex_app_server import AppServer, SOURCE_TOOL, _resumed_source_tool
from qa_source_evidence import json_digest
from reader_store import ReaderStore
from tests.test_annotation_v39 import bridge, source_packet, SimulatedTransport
from tests.test_reader_v3 import fixture


def context(text="Original readable body"):
    body = [{"view": "overview", "section_id": "section-a", "parent_ids": [],
             "id": "block-a", "type": "paragraph", "text_zh": text},
            {"view": "explanation", "section_id": "section-b", "parent_ids": [],
             "id": "block-b", "type": "paragraph", "text_zh": "Unchanged explanation"}]
    return {"paper_id": "paper_a", "reader_body": body,
            "reader_snapshot": {"reader_body_sha256": json_digest(body)},
            "sources": [{"id": "paper", "title": "Stable source identity"}],
            "original_pdf": {"pdf_sha256": "hash-bound-source", "status": "unavailable"}}


class RecordingTransport(SimulatedTransport):
    def rpc(self, method, params, timeout=60):
        if method == "turn/start":
            self.calls.append((method, deepcopy(params)))
            tid = "turn-" + str(len(self.turns) + 1)
            self.turns.append({"id": tid, "status": "completed", "items": [
                {"type": "userMessage", "clientId": params["clientUserMessageId"],
                 "content": deepcopy(params["input"])},
                {"type": "agentMessage", "phase": "final_answer",
                 "text": json.dumps({"answer": "Remembered explanation", "sources": []})}]})
            if getattr(self, "lose_response", False):
                self.lose_response = False
                raise UnknownDelivery("Host accepted, response lost")
            return {"turn": {"id": tid}}
        return super().rpc(method, params, timeout)


class AnnotationSessionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paper = fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def adapter(self, turns=None):
        adapter = bridge({"vault": str(self.root)})
        adapter.server = RecordingTransport(turns=turns)
        return adapter

    def start(self, adapter, task_id="task-a", body=None):
        task = {"id": task_id, "paper_id": "paper_a", "payload": {"user_note": "Current question"}}
        adapter.start("thread-a", task, context() if body is None else body)
        return source_packet(adapter.server.calls[-1][1])

    def test_two_distinct_annotations_and_followup_share_one_loaded_thread(self):
        store = ReaderStore(self.root)
        try:
            adapter = self.adapter()
            store.register_bridge("b1", "codex")
            first = store.create("paper_a", {"user_note": "Remember my earlier explanation preference"})["annotation"]
            for index in range(3):
                if index == 1:
                    store.create("paper_a", {"user_note": "Use the preference from my other annotation"})
                elif index == 2:
                    store.edit("paper_a", first["id"], {"revision": 1, "user_note": "Continue that explanation"}, "followup")
                task = store.claim("b1")["task"]
                thread = adapter.session(self.paper, task["session_thread_id"])
                store.acknowledge(task["id"], "b1", task["claim_token"], thread)
                turn = adapter.start(thread, task, context())
                store.acknowledge(task["id"], "b1", task["claim_token"], thread, turn)
                store.result(task["id"], "b1", task["claim_token"], {"answer": "Previous explanation", "sources": []})
                packet = source_packet(adapter.server.calls[-1][1])
                self.assertEqual("snapshot" if index == 0 else "delta", packet["context"]["reader_delivery"]["mode"])
                if index:
                    self.assertEqual([], packet["context"]["reader_body"])
                    self.assertNotIn("history", packet["task"])
                    self.assertNotIn("sources", packet["context"])
            methods = [method for method, _ in adapter.server.calls]
            self.assertEqual(1, methods.count("thread/start"))
            self.assertEqual(0, methods.count("thread/resume"))
            self.assertEqual(3, methods.count("turn/start"))
            self.assertEqual(1, len({params["threadId"] for method, params in adapter.server.calls if method == "turn/start"}))
        finally:
            store.close()

    def test_process_restart_reconstructs_changed_blocks_and_seen_exchanges(self):
        first = self.adapter()
        first.session(self.paper)
        self.start(first)
        resumed = self.adapter(first.server.turns)
        resumed.session(self.paper, "thread-a")
        task = {"id": "task-b", "paper_id": "paper_a", "payload": {"user_note": "Continue",
            "history": [{"task_id": "task-a", "answer": "Known exchange must not replay"}]}}
        resumed.start("thread-a", task, context("Updated block only"))
        packet = source_packet(resumed.server.calls[-1][1])
        self.assertEqual(["block-a"], [block["id"] for block in packet["context"]["reader_body"]])
        self.assertEqual("delta", packet["context"]["reader_delivery"]["mode"])
        self.assertNotIn("history", packet["task"])
        self.assertNotIn("sources", packet["context"])
        self.assertNotIn("original_pdf", packet["context"])
        resumed.session(self.paper, "thread-a")
        self.assertEqual(1, sum(method == "thread/resume" for method, _ in resumed.server.calls))

    def test_unknown_send_recovers_actual_delivery_before_next_delta(self):
        adapter = self.adapter()
        adapter.session(self.paper)
        adapter.server.lose_response = True
        with self.assertRaises(UnknownDelivery):
            self.start(adapter)
        self.assertIsNone(adapter.reader_sessions["thread-a"]["body_sha256"])
        answer = adapter.recover({"id": "task-a", "paper_id": "paper_a", "thread_id": "thread-a"}, lambda: False)
        self.assertEqual("Remembered explanation", answer["answer"])
        self.assertEqual(1, sum(method == "turn/start" for method, _ in adapter.server.calls))
        packet = self.start(adapter, "task-b")
        self.assertEqual("delta", packet["context"]["reader_delivery"]["mode"])
        self.assertEqual([], packet["context"]["reader_body"])

    def test_no_matching_unknown_turn_never_resubmits_and_rebuild_starts_fresh(self):
        adapter = self.adapter()
        with self.assertRaises(UnknownDelivery):
            adapter.recover({"id": "lost-task", "paper_id": "paper_a", "thread_id": "thread-a"}, lambda: False)
        self.assertNotIn("turn/start", [method for method, _ in adapter.server.calls])
        rebuilt = self.adapter()
        rebuilt.session(self.paper)  # Explicit rebuild has cleared SQLite mapping.
        self.assertEqual("snapshot", self.start(rebuilt)["context"]["reader_delivery"]["mode"])

    def test_rollout_restores_precompaction_delivery_from_user_records_only(self):
        first = self.adapter()
        first.session(self.paper)
        self.start(first)
        self.start(first, "task-b", context("Updated before compaction"))
        codex_root = self.root / "host"
        rollout = codex_root / "sessions/thread-a.jsonl"
        rollout.parent.mkdir(parents=True)
        records = [{"type": "session_meta", "payload": {"id": "thread-a", "session_id": "thread-a",
            "cli_version": "0.207.0", "dynamic_tools": [deepcopy(SOURCE_TOOL)], "history_mode": "full"}}]
        records.append({"type": "response_item", "payload": {"role": "assistant", "content": [{"text": "Ignored assistant text"}]}})
        for turn in first.server.turns:
            records.append({"type": "response_item", "payload": {"type": "message", "role": "user", "content": turn["items"][0]["content"]}})
        rollout.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        resumed = self.adapter()
        rpc = resumed.server.rpc
        def partial(method, params, timeout=60):
            result = rpc(method, params, timeout)
            if method == "thread/resume":
                # Actual Windows app-server metadata uses this extended path.
                host_path = "\\\\?\\" + str(rollout) if os.name == "nt" else str(rollout)
                result["thread"].update(path=host_path, turnsBackwardsCursor="older-history")
            return result
        resumed.server.rpc = partial
        with patch.dict("os.environ", {"CODEX_HOME": str(codex_root)}):
            resumed.session(self.paper, "thread-a")
            packet = self.start(resumed, "task-c", context("Updated before compaction"))
        self.assertEqual([], packet["context"]["reader_body"])
        self.assertEqual("delta", packet["context"]["reader_delivery"]["mode"])
        self.assertEqual("available", packet["context"]["reader_delivery"]["source_tool"])

    @unittest.skipUnless(os.name == "nt", "Windows verbatim-path contract")
    def test_windows_real_rollout_path_keeps_source_tool_and_identity_boundary(self):
        root = self.root / "host"
        path = root / "sessions/rollout-thread.jsonl"
        path.parent.mkdir(parents=True)
        meta = {"type": "session_meta", "payload": {"session_id": "thread-a", "id": "thread-a",
            "dynamic_tools": [deepcopy(SOURCE_TOOL)], "history_mode": "full"}}
        path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        with patch.dict("os.environ", {"CODEX_HOME": str(root)}):
            self.assertTrue(_resumed_source_tool({"id": "thread-a", "path": "\\\\?\\" + str(path)}))
            self.assertFalse(_resumed_source_tool({"id": "another-thread", "path": "\\\\?\\" + str(path)}))
            outside = self.root / "outside.jsonl"
            outside.write_text(json.dumps(meta) + "\n", encoding="utf-8")
            self.assertFalse(_resumed_source_tool({"id": "thread-a", "path": "\\\\?\\" + str(outside)}))

    def test_partial_or_compacted_history_cannot_trigger_full_body_resend(self):
        for thread_fields in ({"turnsBackwardsCursor": "older"},
            {"turns": [{"itemsBackwardsCursor": "older-items", "items": []}]},
            {"turns": [{"items": [{"type": "contextCompaction"}]}]}):
            with self.subTest(thread_fields=thread_fields):
                adapter = self.adapter()
                rpc = adapter.server.rpc
                def partial(method, params, timeout=60):
                    result = rpc(method, params, timeout)
                    if method == "thread/resume":
                        result["thread"].update(thread_fields)
                    return result
                adapter.server.rpc = partial
                adapter.session(self.paper, "thread-a")
                with self.assertRaisesRegex(ConfirmedFailure, "delivered reader body"):
                    self.start(adapter)
                self.assertEqual(["thread/resume"], [method for method, _ in adapter.server.calls])

    def test_complete_legacy_history_without_reader_gets_one_initial_body(self):
        adapter = self.adapter([{"id": "legacy-turn", "status": "completed", "items": [
            {"type": "userMessage", "content": [{"text": "Earlier question without reader body"}]}]}])
        adapter.session(self.paper, "thread-a")
        self.assertEqual("snapshot", self.start(adapter)["context"]["reader_delivery"]["mode"])
        self.assertEqual("delta", self.start(adapter, "task-b")["context"]["reader_delivery"]["mode"])

    def test_usage_notification_keeps_exact_counters_when_turn_notices_are_drained(self):
        counters = {"total": {"inputTokens": 6200, "cachedInputTokens": 4096, "outputTokens": 123},
                    "last": {"inputTokens": 6200, "cachedInputTokens": 4096, "outputTokens": 123}}
        server = object.__new__(AppServer)
        server.lock = threading.RLock()
        server.changed = threading.Condition(server.lock)
        server.pending, server.notices, server.tool_requests, server.token_usage = {}, [], [], {}
        server.process = type("Process", (), {"stdout": io.StringIO(json.dumps({
            "method": "thread/tokenUsage/updated", "params": {"threadId": "thread-a", "turnId": "turn-a", "tokenUsage": counters}}) + "\n")})()
        server._read()
        server.drain_turn("thread-a", "turn-a")
        self.assertEqual(counters, server.token_usage[("thread-a", "turn-a")])
        adapter = self.adapter()
        adapter.server.token_usage = deepcopy(server.token_usage)
        adapter.wait("thread-a", "turn-a", lambda: False)
        self.assertEqual(counters, adapter.health()["token_usage"]["tokenUsage"])
        self.assertEqual("thread/tokenUsage/updated", adapter.health()["token_usage"]["source"])


if __name__ == "__main__":
    unittest.main()
