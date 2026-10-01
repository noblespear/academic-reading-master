"""Stdlib regression suite: durability, race safety, security and real HTTP/mock."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from reader_store import ReaderStore, StoreError, IDENTITY_PALETTE, color_seed
from serve_reader_v3 import ReaderServer
from deploy_reader import load_paper_data, validate_latex_structure
from bridges.codex_app_server import CodexAppServerBridge
from bridges.base import UnknownDelivery
from migrate_reader_v3 import apply_anchor_map, migrate


def fixture(vault, paper="paper_a"):
    directory = Path(vault) / "papers" / paper
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reader.html").write_text("<!doctype html><title>Test reader</title>", encoding="utf-8")
    data = {"schema_version": 3, "content_version": "fixture-1", "id": paper, "title_en": "Fixture Paper",
        "overview": {"sections": [{"id": "overview", "blocks": [{"id": "overview-1", "type": "para", "text_zh": "概要", "source_refs": [{"page": 1, "content_id": "body-1"}]}]}]},
        "explanation": {"sections": [{"id": "explanation", "blocks": [{"id": "explanation-1", "type": "para", "text_zh": "解释", "source_refs": [{"page": 1, "content_id": "body-1"}]}]}]},
        "sections": [{"id": "body", "page": 1, "blocks": [{"id": "body-1", "type": "para", "page": 1,
            "text_en": "A vertex split is the inverse of edge collapse.", "text_zh": "顶点分裂是边塌缩的逆操作。", "source_refs": [{"page": 1}]}]}]}
    (directory / "paper-data.js").write_text("window.PAPER_DATA = " + json.dumps(data, ensure_ascii=False) + ";", encoding="utf-8")
    return directory


class StoreTests(unittest.TestCase):
    def test_draft_color_survives_submission_but_conflicts_are_reallocated(self):
        anchor = {"kind":"text","view":"translation","language":"en","content_id":"body-1","field":"text_en","start":0,"end":10}
        first = self.create(id="color-first", anchor=anchor, identity_color="#497da7")["annotation"]
        second = self.create(id="color-second", anchor=anchor, identity_color="#497da7")["annotation"]
        self.assertEqual(first["identity_color"], "#497da7")
        self.assertNotEqual(first["identity_color"], second["identity_color"])
        with self.assertRaises(StoreError):
            self.create(id="color-invalid", identity_color="url(https://example.invalid)")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        fixture(self.vault)
        fixture(self.vault, "paper_b")
        self.clock = [1000.0]
        self.store = ReaderStore(self.vault, clock=lambda: self.clock[0])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, paper="paper_a", **kwargs):
        return self.store.create(paper, {"user_note": "What is the inverse operation?", "block_id": "body-1", **kwargs})

    def bridge(self, bid="b1"):
        self.store.register_bridge(bid, "mock")
        return bid

    def answer(self, task, bid="b1"):
        self.store.acknowledge(task["id"], bid, task["claim_token"], "thread-a", "turn-a")
        return self.store.result(task["id"], bid, task["claim_token"], {"answer": "Vertex split.", "sources": [{"content_id": "body-1", "page": 1}]})

    def test_cas_and_history_and_unread(self):
        anno = self.create()["annotation"]
        self.bridge()
        task = self.store.claim("b1")["task"]
        self.assertTrue(self.answer(task)["applied"])
        answered = self.store.get("paper_a", anno["id"])
        self.assertTrue(answered["unread"])
        self.store.mark_read("paper_a", anno["id"], {})
        self.assertFalse(self.store.get("paper_a", anno["id"])["unread"])
        follow = self.store.edit("paper_a", anno["id"], {"revision": 1, "user_note": "Why?"}, "followup")
        self.assertEqual(2, follow["annotation"]["revision"])
        self.assertEqual("Vertex split.", follow["annotation"]["history"][0]["answer"])
        self.assertEqual("", follow["annotation"]["answer"])
        with self.assertRaises(StoreError) as caught:
            self.store.edit("paper_a", anno["id"], {"revision": 1, "user_note": "stale"})
        self.assertEqual(409, caught.exception.status)

    def test_deduplication_and_conflicting_request_reuse(self):
        one = self.create(client_request_id="request-1")
        two = self.create(client_request_id="request-1")
        self.assertEqual(one, two)
        self.assertEqual(1, self.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        with self.assertRaises(StoreError):
            self.create(client_request_id="request-1", user_note="Different body")

    def test_delete_does_not_resurrect_on_late_result_or_import(self):
        anno = self.create(id="stable-id")["annotation"]
        self.bridge()
        task = self.store.claim("b1")["task"]
        self.store.acknowledge(task["id"], "b1", task["claim_token"], "thread-a", "turn-a")
        self.store.delete("paper_a", anno["id"], 1)
        response = self.store.result(task["id"], "b1", task["claim_token"], {"answer": "Late answer", "sources": []})
        self.assertFalse(response["applied"])
        self.assertEqual([], self.store.list_annotations("paper_a")["annotations"])
        path = self.vault / "papers/paper_a/annotations.json"
        path.write_text(json.dumps({"annotations": [{"id": "stable-id", "note": "old", "answer": "old answer"}]}), encoding="utf-8")
        self.assertEqual(0, self.store.import_legacy("paper_a"))
        self.assertEqual([], self.store.list_annotations("paper_a")["annotations"])
        with self.assertRaises(StoreError):
            self.create(id="stable-id")

    def test_stale_revision_results_ignored(self):
        anno = self.create()["annotation"]
        self.bridge()
        task = self.store.claim("b1")["task"]
        self.store.acknowledge(task["id"], "b1", task["claim_token"], "thread", "turn")
        updated = self.store.edit("paper_a", anno["id"], {"revision": 1, "user_note": "replacement", "submit": True})
        self.assertEqual(2, updated["annotation"]["revision"])
        self.assertFalse(self.store.result(task["id"], "b1", task["claim_token"], {"answer": "stale", "sources": []})["applied"])
        self.assertEqual("", self.store.get("paper_a", anno["id"])["answer"])
        self.assertIsNotNone(self.store.claim("b1")["task"])

    def test_same_paper_serial_different_paper_can_progress(self):
        self.create()
        self.create()
        self.create("paper_b")
        self.bridge("b1")
        self.bridge("b2")
        first = self.store.claim("b1")["task"]
        second = self.store.claim("b2")["task"]
        self.assertNotEqual(first["paper_id"], second["paper_id"])
        self.assertIsNone(self.store.claim("b2")["task"])

    def test_claim_expiry_is_safe_to_redeliver_but_ack_expiry_is_unknown(self):
        self.create()
        self.bridge()
        one = self.store.claim("b1")["task"]
        self.clock[0] += 46
        self.store.expire_leases()
        two = self.store.claim("b1")["task"]
        self.assertEqual(one["id"], two["id"])
        self.assertGreater(two["lease_generation"], one["lease_generation"])
        with self.assertRaises(StoreError):
            self.store.acknowledge(one["id"], "b1", one["claim_token"])
        self.store.acknowledge(two["id"], "b1", two["claim_token"], "thread", "turn")
        self.clock[0] += 46
        self.store.expire_leases()
        self.assertEqual("unknown", self.store._task(two["id"])["state"])
        self.assertIsNone(self.store.claim("b1")["task"])
        self.assertEqual("failed", self.store.get("paper_a", two["annotation_id"])["status"])

    def test_explicit_retry_unlocks_unknown_without_automatic_replay(self):
        anno = self.create()["annotation"]
        self.bridge()
        task = self.store.claim("b1")["task"]
        self.store.acknowledge(task["id"], "b1", task["claim_token"], "thread", "turn")
        self.clock[0] += 46
        self.store.expire_leases()
        result = self.store.edit("paper_a", anno["id"], {"revision": 1}, "retry")
        next_task = self.store.claim("b1")["task"]
        self.assertNotEqual(task["id"], next_task["id"])
        self.assertEqual("thread", next_task["session_thread_id"])
        self.assertEqual(2, result["annotation"]["revision"])

    def test_lease_adoption_requires_dead_old_bridge_and_same_adapter(self):
        self.create()
        self.bridge("b1")
        task = self.store.claim("b1")["task"]
        self.store.acknowledge(task["id"], "b1", task["claim_token"], "thread", "turn")
        self.bridge("b2")
        with self.assertRaises(StoreError):
            self.store.adopt(task["id"], "b2")
        self.clock[0] += 61
        adopted = self.store.adopt(task["id"], "b2")["task"]
        self.assertNotEqual(task["claim_token"], adopted["claim_token"])
        self.assertEqual("turn", adopted["turn_id"])
        self.assertTrue(self.store.result(task["id"], "b2", adopted["claim_token"], {"answer": "recovered", "sources": []})["applied"])

    def test_legacy_19_answers_survive_restart_without_tasks(self):
        path = self.vault / "papers/paper_a/annotations.json"
        original = {"paper_id": "paper_a", "annotations": [{"id": "old-" + str(i), "quote": "quote", "note": "question", "answer": "answer-" + str(i)} for i in range(19)]}
        raw = json.dumps(original, ensure_ascii=False).encode("utf-8")
        path.write_bytes(raw)
        self.assertEqual(19, self.store.import_legacy("paper_a"))
        self.assertEqual(0, self.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        backups = list((self.vault / ".reader/imports").glob("*.json"))
        self.assertEqual(raw, backups[0].read_bytes())
        self.store.close()
        self.store = ReaderStore(self.vault)
        self.assertEqual(0, self.store.import_legacy("paper_a"))
        self.assertEqual(["answer-" + str(i) for i in range(19)], [a["answer"] for a in self.store.list_annotations("paper_a")["annotations"]])

    def test_unavailable_bridge_and_event_cursor(self):
        anno = self.create()["annotation"]
        self.assertEqual("waiting_bridge", self.store.list_annotations("paper_a")["annotations"][0]["status"])
        events = self.store.events_after(0, "paper_a")
        self.assertEqual(anno["id"], events[0]["data"]["annotation"]["id"])
        self.assertEqual([], self.store.events_after(events[-1]["seq"], "paper_a"))

    def test_legacy_anchor_map_repairs_view_without_altering_old_fields(self):
        old = {"id": "old-anno", "quote": "quoted", "note": "question", "answer": "original answer", "created_at": "original-created", "updated_at": "original-updated"}
        path = self.vault / "papers/paper_a/annotations.json"
        path.write_text(json.dumps({"annotations": [old]}), encoding="utf-8")
        self.store.import_legacy("paper_a")
        before = self.store.get("paper_a", "old-anno")
        anchor = {"view": "explanation", "language": "zh", "content_id": "explanation-1", "field": "text_zh", "content_version": "fixture-1", "fragments": []}
        map_path = self.vault / "migration_anchor_map.json"
        map_path.write_text(json.dumps({"paper_id": "paper_a", "content_version": "fixture-1", "anchors": {"old-anno": anchor}}), encoding="utf-8")
        self.assertEqual(1, apply_anchor_map(self.store, "paper_a", map_path)["anchors_updated"])
        after = self.store.get("paper_a", "old-anno")
        self.assertEqual(anchor, after["anchor"])
        self.assertEqual({k:v for k,v in before.items() if k != "anchor"}, {k:v for k,v in after.items() if k != "anchor"})
        self.assertEqual(old, after["legacy"])
        self.assertEqual(0, self.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        self.store.delete("paper_a", "old-anno", 1)
        self.assertEqual(["old-anno"], apply_anchor_map(self.store, "paper_a", map_path)["anchors_skipped"])

    def test_citation_identity_and_path_boundaries(self):
        with self.assertRaises(StoreError):
            self.store.paper_dir("../paper_a")
        with self.assertRaises(StoreError):
            self.store.validate_sources("paper_a", [{"content_id": "nonexistent", "page": 1}])
        with self.assertRaises(StoreError):
            self.store.validate_sources("paper_a", [{"page": 0}])
        with self.assertRaises(StoreError):
            self.store.validate_sources("paper_a", [{"url": "javascript:alert(1)"}])

    def test_hash_collision_colors_are_distinct_for_overlaps_and_durable(self):
        buckets = {}
        pair = None
        for index in range(100):
            aid = "collision-" + str(index)
            bucket = color_seed(aid) % len(IDENTITY_PALETTE)
            if bucket in buckets:
                pair = (buckets[bucket], aid)
                break
            buckets[bucket] = aid
        self.assertIsNotNone(pair)
        self.assertEqual(color_seed(pair[0]) % 6, color_seed(pair[1]) % 6)
        text_anchor = {"kind": "text", "view": "translation", "language": "zh", "content_id": "body-1", "field": "text_zh", "start": 0, "end": 10, "fragments": []}
        first = self.create(id=pair[0], submit=False, anchor=text_anchor)["annotation"]
        second = self.create(id=pair[1], submit=False, anchor={**text_anchor, "start": 5, "end": 15})["annotation"]
        self.assertNotEqual(first["identity_color"], second["identity_color"])
        block = self.create(id="block-overlap", submit=False, anchor={**text_anchor, "kind": "block", "field": "block"})["annotation"]
        self.assertNotIn(block["identity_color"], (first["identity_color"], second["identity_color"]))
        moved = self.store.edit("paper_a", first["id"], {"revision": 1, "anchor": {**text_anchor, "start": 50, "end": 60}})["annotation"]
        self.assertEqual(first["identity_color"], moved["identity_color"])
        self.store.close()
        self.store = ReaderStore(self.vault)
        self.assertEqual(first["identity_color"], self.store.get("paper_a", first["id"])["identity_color"])
        self.assertEqual(second["identity_color"], self.store.get("paper_a", second["id"])["identity_color"])

    def test_overlapping_palette_exhaustion_and_nonoverlap_reuse(self):
        anchor = {"kind": "text", "view": "translation", "language": "zh", "content_id": "body-1", "field": "text_zh", "start": 0, "end": 10}
        annotations = [self.create(id="dense-"+str(i), submit=False, anchor=anchor)["annotation"] for i in range(10)]
        self.assertEqual(10, len({a["identity_color"] for a in annotations}))
        self.assertTrue(annotations[6]["identity_color"].startswith("hsl("))
        other_language = self.create(id="other-language", submit=False, anchor={**anchor, "language": "en"})["annotation"]
        self.assertIn(other_language["identity_color"], IDENTITY_PALETTE)

    def test_legacy_latex_display_and_answer_times_preserved_then_cleared(self):
        old = {"id": "old-math", "quote": "old quote", "note": "old question", "answer": "Original x_i answer",
               "answer_latex": r"Original \(x_i\) answer", "answered_at": "2026-09-01T12:00:00Z",
               "created_at": "2026-08-31T11:00:00Z", "updated_at": "2026-08-31T11:01:00Z"}
        path = self.vault / "papers/paper_a/annotations.json"
        path.write_text(json.dumps({"annotations": [old]}), encoding="utf-8")
        self.store.import_legacy("paper_a")
        imported = self.store.get("paper_a", "old-math")
        self.assertEqual(old["answer"], imported["answer"])
        self.assertEqual(old["answered_at"], imported["answered_at"])
        self.assertEqual(old["answered_at"], imported["history"][0]["answered_at"])
        self.assertEqual(old["answer_latex"], imported["answer_latex"])
        self.assertEqual(old["answer_latex"], imported["history"][0]["answer_latex"])
        changed = self.store.edit("paper_a", "old-math", {"revision": 1, "user_note": "new question"}, "followup")["annotation"]
        self.assertNotIn("answer_latex", changed)
        self.assertEqual(old["answer_latex"], changed["history"][0]["answer_latex"])
        self.bridge()
        task = self.store.claim("b1")["task"]
        self.answer(task)
        new = self.store.get("paper_a", "old-math")
        self.assertNotIn("answer_latex", new)
        self.assertEqual("Vertex split.", new["answer"])
        self.assertEqual(old, new["legacy"])

    def test_first_import_maps_anchors_before_overlap_color_allocation(self):
        buckets = {}
        for index in range(100):
            aid = "migrate-color-" + str(index)
            bucket = color_seed(aid) % len(IDENTITY_PALETTE)
            if bucket in buckets:
                pair = (buckets[bucket], aid)
                break
            buckets[bucket] = aid
        old = [{"id": aid, "block_id": "old-unrelated-"+str(i), "quote": "q", "note": "n", "answer": "a"} for i, aid in enumerate(pair)]
        path = self.vault / "papers/paper_a/annotations.json"
        raw = json.dumps({"annotations": old}).encode()
        path.write_bytes(raw)
        anchor = {"kind": "text", "view": "explanation", "language": "zh", "content_id": "explanation-1", "field": "text_zh", "content_version": "fixture-1", "start": 0, "end": 10}
        map_path = self.vault / "migration_anchor_map.json"
        map_path.write_text(json.dumps({"paper_id": "paper_a", "content_version": "fixture-1", "anchors": {aid: anchor for aid in pair}}), encoding="utf-8")
        result = migrate(self.vault, "paper_a", map_path)
        self.assertEqual(0, result["tasks_created"])
        self.assertEqual(2, result["imported"])
        colors = [self.store.get("paper_a", aid)["identity_color"] for aid in pair]
        self.assertNotEqual(colors[0], colors[1])
        self.assertIn(raw, [p.read_bytes() for p in (self.vault / ".reader/imports").glob("*.json")])
        for i, aid in enumerate(pair):
            self.assertEqual(old[i], self.store.get("paper_a", aid)["legacy"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fixture(self.temp.name)
        self.server = ReaderServer(self.temp.name, "paper_a", bridge="none")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = self.server.base_url

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.cleanup()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None, token=True):
        heads = {"X-Reader-Token": self.server.token} if token else {}
        if body is not None:
            heads["Content-Type"] = "application/json"
        heads.update(headers or {})
        request = Request(self.url+path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=heads)
        with urlopen(request, timeout=4) as response:
            return response.status, json.load(response)

    def test_session_and_crud_and_host_origin_token_guards(self):
        self.assertEqual(3, self.request("/api/session", token=False)[1]["api_version"])
        for heads, token in (({}, False), ({"Origin": "https://evil.example"}, True), ({"Host": "evil.example"}, True)):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/papers/paper_a/annotations", headers=heads, token=token)
            self.assertEqual(403, caught.exception.code)
        created = self.request("/api/papers/paper_a/annotations", "POST", {"user_note": "Question", "submit": False})[1]["annotation"]
        updated = self.request("/api/papers/paper_a/annotations/"+created["id"], "PATCH", {"revision": 1, "user_note": "Changed"})[1]["annotation"]
        self.assertEqual(2, updated["revision"])
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/papers/paper_a/annotations/"+created["id"], "DELETE", {"revision": 1})
        self.assertEqual(409, caught.exception.code)
        self.request("/api/papers/paper_a/annotations/"+created["id"], "DELETE", {"revision": 2})
        self.assertEqual([], self.request("/api/papers/paper_a/annotations")[1]["annotations"])

    def test_private_files_and_traversal(self):
        for path in ("/../.reader/server.json", "/papers/paper_a/../../.reader/server.json", "/annotations.json", "/papers/../reader.html"):
            with self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertIn(caught.exception.code, (400, 403, 404))

    def test_sse_replays_by_event_id(self):
        anno = self.request("/api/papers/paper_a/annotations", "POST", {"user_note": "SSE", "submit": False})[1]["annotation"]
        request = Request(self.url + "/api/events?paper_id=paper_a&after=0&token=" + self.server.token)
        with urlopen(request, timeout=3) as response:
            lines = [response.readline().decode() for _ in range(5)]
            self.assertIn("event: annotation\n", lines)
            data_line = next(line for line in lines if line.startswith("data: "))
            self.assertEqual(anno["id"], json.loads(data_line[6:])["annotation"]["id"])

    def test_bridge_credentials_are_distinct(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/bridge/register", "POST", {"bridge_id": "test", "adapter": "mock"})
        self.assertEqual(403, caught.exception.code)
        self.assertTrue(self.request("/api/bridge/register", "POST", {"bridge_id": "test", "adapter": "mock"},
            headers={"X-Reader-Token": self.server.bridge_token})[1]["ok"])


class MockSupervisorTests(unittest.TestCase):
    def test_service_owns_mock_child_and_child_answers_over_real_http(self):
        temp = tempfile.TemporaryDirectory()
        fixture(temp.name)
        server = ReaderServer(temp.name, "paper_a", bridge="mock")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.start_supervisor()
        try:
            anno = server.store.create("paper_a", {"block_id": "body-1", "user_note": "Mock integration"})["annotation"]
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                result = server.store.get("paper_a", anno["id"])
                if result["status"] in ("answered", "failed"):
                    break
                time.sleep(0.1)
            self.assertEqual("answered", result["status"], (Path(temp.name)/".reader/bridge.log").read_text(encoding="utf-8"))
            self.assertTrue(result["answer"].startswith("[MOCK"))
            self.assertTrue(result["unread"])
            server.request_stop()
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
            self.assertIsNotNone(server.bridge_process.poll())
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=3)
            server.cleanup()
            temp.cleanup()


class AdapterTests(unittest.TestCase):
    def test_final_message_extraction_and_latex_structure(self):
        items = [{"type": "agentMessage", "phase": "commentary", "text": "working"},
                 {"type": "agentMessage", "phase": "final_answer", "text": '{"answer":"done","sources":[]}'}]
        self.assertEqual("done", CodexAppServerBridge._answer(items)["answer"])
        self.assertEqual([], validate_latex_structure(r"\frac{a+b}{c}\left(x\right)"))
        self.assertTrue(validate_latex_structure(r"\frac{a}{b"))
        self.assertTrue(validate_latex_structure(r"\left(x"))
        self.assertEqual([], validate_latex_structure(r"a\leftarrow b"))

    def adapter(self, turns):
        class StubRPC:
            def __init__(self):
                self.calls = []

            def rpc(self, method, params, timeout=60):
                self.calls.append((method, params))
                if method in ("thread/resume", "thread/read"):
                    return {"thread": {"id": "thread-a", "turns": turns}}
                raise AssertionError("Unexpected host RPC " + method)
        adapter = CodexAppServerBridge.__new__(CodexAppServerBridge)
        adapter.config = {"vault": "fixture"}
        adapter.server = StubRPC()
        adapter.active, adapter.resumed = set(), {}
        adapter.sessions = set()
        return adapter

    def test_recovery_reads_exact_completed_turn_without_resending(self):
        adapter = self.adapter([{"id": "turn-a", "status": "completed", "items": [
            {"type": "agentMessage", "phase": "final_answer", "text": '{"answer":"recovered","sources":[]}'}]}])
        result = adapter.recover({"id": "task-a", "paper_id": "paper_a", "thread_id": "thread-a", "turn_id": "turn-a"}, lambda: False)
        self.assertEqual("recovered", result["answer"])
        self.assertEqual(["thread/resume", "thread/read"], [c[0] for c in adapter.server.calls])
        self.assertNotIn("model", adapter.server.calls[0][1])
        self.assertEqual("read-only", adapter.server.calls[0][1]["sandbox"])

    def test_unknown_delivery_is_not_resubmitted(self):
        adapter = self.adapter([])
        with self.assertRaises(UnknownDelivery):
            adapter.recover({"id": "task-a", "paper_id": "paper_a", "thread_id": "thread-a", "turn_id": None}, lambda: False)
        self.assertNotIn("turn/start", [c[0] for c in adapter.server.calls])

    def test_recovery_finds_task_marker_after_lost_start_response(self):
        adapter = self.adapter([{"id": "lost-turn", "status": "completed", "items": [
            {"type": "userMessage", "clientId": "task-a", "content": []},
            {"type": "agentMessage", "phase": "final_answer", "text": '{"answer":"found","sources":[]}'}]}])
        result = adapter.recover({"id": "task-a", "paper_id": "paper_a", "thread_id": "thread-a", "turn_id": None}, lambda: False)
        self.assertEqual("found", result["answer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
