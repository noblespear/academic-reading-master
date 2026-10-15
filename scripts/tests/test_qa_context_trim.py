"""Small real PDFs and simulated RPCs; no model turns or full-paper benchmark."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_client import build_context
from bridges.base import UnknownDelivery, ConfirmedFailure
from bridges.codex_app_server import SOURCE_TOOL, _resumed_source_tool
from qa_source_evidence import requested_pdf_evidence
from reader_store import ReaderStore, StoreError
from tests.test_annotation_v39 import bridge, source_packet, SimulatedTransport
from tests import test_qa_source_evidence as evidence_tests


class ToolTransport(SimulatedTransport):
    def __init__(self, arguments):
        super().__init__(answer={"answer": "已核对相关原页。", "sources": []})
        self.arguments, self.responses, self.requested = arguments, [], False

    def drain_tools(self, thread_id, turn_id):
        if self.requested:
            return []
        self.requested = True
        return [{"id": 77, "method": "item/tool/call", "params": {
            "threadId": thread_id, "turnId": turn_id, "tool": "reader_source",
            "arguments": self.arguments}}]

    def respond_tool(self, rid, result):
        self.responses.append((rid, result))


@unittest.skipIf(evidence_tests.fitz is None, "PyMuPDF unavailable")
class CompactQuestionTests(unittest.TestCase):
    setUp = evidence_tests.SourceEvidenceTests.setUp
    tearDown = evidence_tests.SourceEvidenceTests.tearDown
    write_data = evidence_tests.SourceEvidenceTests.write_data
    write_pdf = evidence_tests.SourceEvidenceTests.write_pdf
    task = evidence_tests.SourceEvidenceTests.task

    def start(self, adapter, task=None):
        task = task or self.task()
        adapter.start("thread-a", task, build_context(self.paper, task))
        return source_packet(adapter.server.calls[-1][1])

    def new_bridge(self):
        adapter = bridge({"vault": str(self.vault)})
        adapter.session(self.paper)
        return adapter

    def test_section_only_sources_reach_hash_selection_and_original_page_images(self):
        section = self.data["overview"]["sections"][0]
        section["source_refs"] = [{"source_id": "paper", "page": 2, "label": "SECTION_ONLY_SOURCE"}]
        section["blocks"].append({"id": "eq", "type": "formula", "latex": "x=1"})
        self.write_data()
        adapter = self.new_bridge()
        first = self.start(adapter, self.task("eq"))
        self.assertEqual(2, first["context"]["reader_body"][0]["section_source_refs"][0]["page"])
        self.assertEqual(2, first["context"]["selected_blocks"][0]["source_refs"][0]["page"])
        self.assertEqual(2, first["context"]["selected_source_images"][0]["page"])
        section["source_refs"][0]["page"] = 3
        self.write_data()
        changed = self.start(adapter, self.task("eq"))
        self.assertNotEqual(first["context"]["reader_delivery"]["body_sha256"], changed["context"]["reader_delivery"]["body_sha256"])
        self.assertTrue(changed["context"]["reader_body"])
        self.assertEqual(3, changed["context"]["selected_blocks"][0]["section_source_refs"][0]["page"])
        self.assertEqual(3, changed["context"]["selected_source_images"][0]["page"])

    def test_static_code_legacy_algorithm_and_narrative_are_visible_without_component_programs(self):
        blocks = self.data["overview"]["sections"][0]["blocks"]
        blocks[0]["learning"] = {"presentation": "narrative", "blocks": [
            {"id": "extra", "type": "paragraph", "text_zh": "VISIBLE_LEARNING_SENTINEL"},
            {"id": "aid", "type": "learning_aid", "aid": {"id": "component", "title": "Existing demo",
                "config": {"code": "HIDDEN_COMPONENT_PROGRAM"}}}]}
        blocks.extend([{"id": "code", "type": "code", "code": "VISIBLE_CODE_SENTINEL"},
            {"id": "algo", "type": "algorithm", "algo_lines_latex": [r"\(x_{t+1}=x_t+1\)"]},
            {"id": "hidden", "type": "paragraph", "learning": {"blocks": [
                {"id": "hidden-child", "type": "paragraph", "text_zh": "HIDDEN_NON_NARRATIVE"}]}}])
        self.write_data()
        task = self.task("code")
        task["payload"]["anchor"]["field"] = "code"
        packet = self.start(self.new_bridge(), task)
        body = {item["id"]: item for item in packet["context"]["reader_body"]}
        self.assertEqual("VISIBLE_CODE_SENTINEL", body["code"]["code"])
        self.assertEqual([r"\(x_{t+1}=x_t+1\)"], body["algo"]["algo_lines_latex"])
        self.assertEqual(["p"], body["extra"]["parent_ids"])
        self.assertEqual("VISIBLE_CODE_SENTINEL", packet["context"]["selected_fields"][0]["value"])
        encoded = json.dumps(packet)
        self.assertIn("VISIBLE_LEARNING_SENTINEL", encoded)
        self.assertNotIn("HIDDEN_COMPONENT_PROGRAM", encoded)
        self.assertNotIn("HIDDEN_NON_NARRATIVE", encoded)

    def test_actual_pdf_page_bound_allows_deferred_appendix_citation_to_be_saved(self):
        self.data["sections"] = []
        section = self.data["overview"]["sections"][0]
        section["source_refs"] = [{"page": 8}]
        section["blocks"][0]["source_refs"] = [{"page": 2}]
        self.write_data()
        self.write_pdf(tuple("physical page " + str(page) for page in range(1, 9)))
        context = build_context(self.paper, self.task())
        page = requested_pdf_evidence(self.paper, {"pages": [8]}, context["original_pdf"]["pdf_sha256"])["text_pages"][0]
        citation = {key: page[key] for key in ("label", "locator", "source_id", "content_id", "url", "page")}
        store = ReaderStore(self.vault)
        try:
            annotation = store.create("sample", {"user_note": "附录解释", "anchor": self.task()["payload"]["anchor"]})["annotation"]
            store.register_bridge("b1", "codex")
            task = store.claim("b1")["task"]
            store.acknowledge(task["id"], "b1", task["claim_token"], "thread-a", "turn-a")
            self.assertTrue(store.result(task["id"], "b1", task["claim_token"], {"answer": "已核对附录。", "sources": [citation]})["applied"])
            self.assertEqual(8, store.get("sample", annotation["id"])["sources"][0]["page"])
            section["source_refs"][0]["page"] = 99
            self.write_data()
            with self.assertRaises(StoreError):
                store.validate_sources("sample", [{**citation, "page": 9}])
            (self.paper / "source.pdf").unlink()
            section["source_refs"][0]["page"] = 8
            self.write_data()
            store.validate_sources("sample", [citation])
            with self.assertRaises(StoreError):
                store.validate_sources("sample", [{**citation, "page": 9}])
        finally:
            store.close()

    def test_resume_replays_reorder_delete_restore_without_repeating_block_order(self):
        section = self.data["overview"]["sections"][0]
        blocks = {name: {"id": name, "type": "paragraph", "text_zh": "Body " + name} for name in "abc"}
        original, turns = self.new_bridge(), []
        for index, names in enumerate(("abc", "cab", "cb", "cba")):
            section["blocks"] = [deepcopy(blocks[name]) for name in names]
            self.write_data()
            task = self.task(names[0])
            task["id"] = "task-" + str(index)
            self.start(original, task)
            turns.append({"id": "turn-" + str(index), "status": "completed", "items": [
                {"type": "userMessage", "content": [{"text": original.server.calls[-1][1]["input"][0]["text"]}]}]})
        resumed = bridge({"vault": str(self.vault)}, turns)
        resumed.session(self.paper, "thread-a")
        packet = self.start(resumed, self.task("c"))
        self.assertEqual([], packet["context"]["reader_body"])
        self.assertNotIn("block_order", packet["context"]["reader_delivery"])
        # A malformed duplicate/missing key list cannot establish a baseline.
        bad_turns = deepcopy(turns)
        text = bad_turns[-1]["items"][0]["content"][0]["text"]
        bad_packet = source_packet({"input": [{"text": text}]})
        old_json = json.dumps(bad_packet, ensure_ascii=False)
        order = bad_packet["context"]["reader_delivery"]["block_order"]
        order[-1] = order[0]
        bad_turns[-1]["items"][0]["content"][0]["text"] = text.replace(old_json, json.dumps(bad_packet, ensure_ascii=False))
        resumed = bridge({"vault": str(self.vault)}, bad_turns)
        resumed.session(self.paper, "thread-a")
        with self.assertRaisesRegex(ConfirmedFailure, "delivered reader body"):
            self.start(resumed, self.task("c"))

    def test_default_contains_readable_body_and_ids_without_translation_or_programs(self):
        self.data["sections"][0]["blocks"] = [{"id": "translation-only", "type": "paragraph",
            "text_en": "UNRELATED_FULL_TRANSLATION", "text_zh": "不应默认发送的译文"}]
        self.data["learning_catalog"] = {"aids": [{"id": "config", "html": "PROGRAM_SENTINEL"}]}
        self.data["overview"]["sections"][0]["blocks"].append({"id": "component", "type": "learning_aid",
            "aid": {"id": "aid", "title": "已有演示", "config": {"js": "PROGRAM_SENTINEL"}}})
        self.write_data()
        packet = self.start(self.new_bridge())
        encoded = json.dumps(packet, ensure_ascii=False)
        for sentinel in ("UNRELATED_FULL_TRANSLATION", "PROGRAM_SENTINEL", "first body page", "last appendix page"):
            self.assertNotIn(sentinel, encoded)
        self.assertEqual("snapshot", packet["context"]["reader_delivery"]["mode"])
        self.assertEqual("p", packet["context"]["reader_body"][0]["id"])
        self.assertEqual("正文", packet["context"]["reader_body"][0]["text_zh"])
        self.assertNotIn("text_pages", packet["context"]["original_pdf"])

    def test_second_question_keeps_new_selection_but_omits_body_and_delivered_history(self):
        blocks = self.data["overview"]["sections"][0]["blocks"]
        blocks.extend({"id": "p" + str(i), "type": "paragraph", "text_zh": "正文中的必要推理。" * 80} for i in range(25))
        self.write_data()
        adapter = self.new_bridge()
        first = self.start(adapter)
        second_task = self.task("p4")
        second_task["id"] = "task-b"
        second_task["payload"]["history"] = [{"task_id": "task-a", "answer": "旧答案", "answer_content": {"config": "HUGE_OLD_COMPONENT"}}]
        adapter.session(self.paper, "thread-a")  # Supervisor resolves the loaded session without another resume.
        second = self.start(adapter, second_task)
        self.assertEqual([], second["context"]["reader_body"])
        self.assertEqual("p4", second["context"]["selected_blocks"][0]["id"])
        self.assertNotIn("history", second["task"])
        self.assertLess(len(json.dumps(second)), len(json.dumps(first)) / 5)

    def test_content_change_sends_only_changed_blocks_and_deletions(self):
        blocks = self.data["overview"]["sections"][0]["blocks"]
        blocks.append({"id": "other", "type": "paragraph", "text_zh": "保留的解释"})
        self.write_data()
        adapter = self.new_bridge()
        self.start(adapter)
        blocks[1]["text_zh"] = "更新后的解释"
        blocks.pop(0)
        self.write_data()
        packet = self.start(adapter, self.task("other"))
        self.assertEqual(["other"], [item["id"] for item in packet["context"]["reader_body"]])
        self.assertEqual(1, len(packet["context"]["reader_delivery"]["removed_blocks"]))
        self.assertIn('"p"', packet["context"]["reader_delivery"]["removed_blocks"][0])

    def test_resume_uses_confirmed_snapshot_and_rebuild_supplies_fresh_body(self):
        original = self.new_bridge()
        self.start(original)
        prompt = original.server.calls[-1][1]["input"][0]["text"]
        turns = [{"id": "old-turn", "status": "completed", "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": prompt}]}]}]
        resumed = bridge({"vault": str(self.vault)}, turns)
        resumed.session(self.paper, "thread-a")
        packet = self.start(resumed)
        self.assertEqual([], packet["context"]["reader_body"])
        self.assertIn("unavailable", packet["context"]["reader_delivery"]["source_tool"])
        rebuilt = self.start(self.new_bridge())
        self.assertEqual("snapshot", rebuilt["context"]["reader_delivery"]["mode"])
        self.assertTrue(rebuilt["context"]["reader_body"])

    def test_resume_without_snapshot_baseline_never_assumes_delta_is_complete(self):
        original = self.new_bridge()
        self.start(original)
        self.start(original)
        prompt = original.server.calls[-1][1]["input"][0]["text"]
        resumed = bridge({"vault": str(self.vault)}, [{"items": [{"type": "userMessage", "content": [{"text": prompt}]}]}])
        resumed.session(self.paper, "thread-a")
        with self.assertRaisesRegex(ConfirmedFailure, "delivered reader body"):
            self.start(resumed)

    def test_unknown_submission_does_not_commit_delivery_or_replay_turn(self):
        adapter = self.new_bridge()
        adapter.server.rpc = unittest.mock.Mock(side_effect=UnknownDelivery("lost start response"))
        with self.assertRaises(UnknownDelivery):
            self.start(adapter)
        self.assertIsNone(adapter.reader_sessions["thread-a"]["body_sha256"])
        self.assertEqual(1, adapter.server.rpc.call_count)

    def test_missing_original_is_supplemented_inside_same_turn_only_when_requested(self):
        adapter = self.new_bridge()
        adapter.server = ToolTransport({"pages": [2]})
        self.start(adapter)
        self.assertEqual([], adapter.server.responses)
        result = adapter.wait("thread-a", "turn-a", lambda: False)
        self.assertEqual("已核对相关原页。", result["answer"])
        tool = adapter.server.responses[0][1]
        self.assertTrue(tool["success"])
        evidence = json.loads(tool["contentItems"][0]["text"])
        self.assertEqual([2], [page["page"] for page in evidence["text_pages"]])
        self.assertIn("second method page", evidence["text_pages"][0]["text"])
        self.assertNotIn("last appendix page", json.dumps(evidence))
        self.assertEqual(1, sum(method == "turn/start" for method, _ in adapter.server.calls))

    def test_source_requests_reject_changed_pdf_paths_excess_pages_and_call_flood(self):
        adapter = self.new_bridge()
        adapter.server = ToolTransport({"pages": [2]})
        self.start(adapter)
        self.write_pdf(("replaced source",))
        adapter._source_requests("thread-a", "turn-a")
        self.assertFalse(adapter.server.responses[-1][1]["success"])
        digest = build_context(self.paper, self.task())["original_pdf"]["pdf_sha256"]
        for request in ({"path": "../secret", "pages": [1]}, {"pages": [1, 1, 1, 1]}, {"pages": [99]}, {"query": "x" * 161}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                requested_pdf_evidence(self.paper, request, digest)
        for _ in range(4):
            adapter.server.requested = False
            adapter._source_requests("thread-a", "turn-a")
        self.assertIn("exhausted", adapter.server.responses[-1][1]["contentItems"][0]["text"])
        large_page = {"status": "complete", "page_count": 1, "pdf_sha256": digest,
                      "text_pages": [{"page": 1, "text": "x" * 48001, "locator": "source.pdf physical page 1"}]}
        with patch("qa_source_evidence.pdf_text_evidence", return_value=large_page):
            with self.assertRaisesRegex(ValueError, "48KB"):
                requested_pdf_evidence(self.paper, {"pages": [1]}, digest)

    def test_literal_excerpt_preserves_original_characters_offsets_and_hash(self):
        digest = build_context(self.paper, self.task())["original_pdf"]["pdf_sha256"]
        result = requested_pdf_evidence(self.paper, {"query": "second method"}, digest)
        page = result["text_pages"][0]
        self.assertEqual("literal-search excerpt", page["coverage"])
        self.assertEqual(hashlib.sha256(page["text"].encode("utf-8")).hexdigest(), page["text_sha256"])
        self.assertEqual(2, page["page"])
        self.assertEqual([], result["images"])

    def test_requested_original_page_pixels_use_verified_dynamic_tool_image(self):
        adapter = self.new_bridge()
        adapter.server = ToolTransport({"pages": [2], "include_images": True})
        self.start(adapter)
        adapter._source_requests("thread-a", "turn-a")
        items = adapter.server.responses[0][1]["contentItems"]
        self.assertEqual("inputImage", items[1]["type"])
        self.assertTrue(items[1]["imageUrl"].startswith("data:image/png;base64,"))
        self.assertEqual([2], [item["page"] for item in json.loads(items[0]["text"])["pixel_delivery"]])


class ResumeToolVerificationTests(unittest.TestCase):
    def test_only_matching_host_rollout_declares_source_tool_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sessions").mkdir()
            path = root / "sessions" / "thread.jsonl"
            thread = {"id": "thread-a", "path": str(path)}
            record = {"type": "session_meta", "payload": {"id": "thread-a", "dynamic_tools": [deepcopy(SOURCE_TOOL)]}}
            with patch.dict("os.environ", {"CODEX_HOME": str(root)}):
                path.write_text(json.dumps(record) + "\n", encoding="utf-8")
                self.assertTrue(_resumed_source_tool(thread))
                record["payload"]["dynamic_tools"][0]["name"] = "other-tool"
                path.write_text(json.dumps(record) + "\n", encoding="utf-8")
                self.assertFalse(_resumed_source_tool(thread))
                self.assertFalse(_resumed_source_tool({**thread, "path": str(root / "outside.jsonl")}))


if __name__ == "__main__":
    unittest.main()
