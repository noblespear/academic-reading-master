"""Selection payloads reuse only exactly matched readable content; no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_source_evidence import json_digest
from tests import test_qa_context_trim as compact_tests
from tests import test_qa_source_evidence as evidence_tests


@unittest.skipIf(evidence_tests.fitz is None, "PyMuPDF unavailable")
class SelectionReuseTests(unittest.TestCase):
    setUp = evidence_tests.SourceEvidenceTests.setUp
    tearDown = evidence_tests.SourceEvidenceTests.tearDown
    write_data = evidence_tests.SourceEvidenceTests.write_data
    write_pdf = evidence_tests.SourceEvidenceTests.write_pdf
    task = evidence_tests.SourceEvidenceTests.task
    start = compact_tests.CompactQuestionTests.start
    new_bridge = compact_tests.CompactQuestionTests.new_bridge

    def test_first_selection_once_and_followup_only_references(self):
        sentinel = "UNIQUE_SELECTION_BODY_" * 80
        self.data["overview"]["sections"][0]["blocks"][0]["text_zh"] = sentinel
        self.write_data()
        adapter = self.new_bridge()
        first = self.start(adapter)
        self.assertEqual(1, json.dumps(first).count(sentinel))
        ref = first["context"]["selected_blocks"][0]["reader_body_ref"]
        body = first["context"]["reader_body"][0]
        self.assertEqual(json_digest(body), ref["block_sha256"])
        second = self.start(adapter, {**self.task(), "id": "task-b"})
        self.assertNotIn(sentinel, json.dumps(second))
        self.assertEqual([], second["context"]["reader_body"])
        self.assertEqual(ref, second["context"]["selected_fields"][0]["reader_body_ref"])

    def test_changed_body_delivered_once_with_new_reference(self):
        adapter = self.new_bridge()
        first = self.start(adapter)
        text = "CHANGED_BODY_SENTINEL"
        self.data["overview"]["sections"][0]["blocks"][0]["text_zh"] = text
        self.write_data()
        second = self.start(adapter, {**self.task(), "id": "changed"})
        self.assertEqual(1, json.dumps(second).count(text))
        self.assertNotEqual(first["context"]["selected_blocks"][0]["reader_body_ref"]["block_sha256"],
                            second["context"]["selected_blocks"][0]["reader_body_ref"]["block_sha256"])

    def test_full_selected_text_and_anchor_quotes_reference_same_field(self):
        text = "FULL_SELECTION_SENTINEL_" * 80
        self.data["overview"]["sections"][0]["blocks"][0]["text_zh"] = text
        self.write_data()
        task = self.task()
        task["payload"]["selected_text"] = text
        anchor = task["payload"]["anchor"]
        anchor["exact"] = text
        anchor["quote"] = {"exact": text, "prefix": "prefix", "suffix": "suffix"}
        anchor["fragments"] = [{"view": "overview", "content_id": "p", "field": "text_zh", "exact": text, "quote": {"exact": text}}]
        stored = deepcopy(task)
        adapter = self.new_bridge()
        first = self.start(adapter, task)
        self.assertEqual(stored, task, "wire compaction must not change stored anchors")
        self.assertEqual(1, json.dumps(first).count(text))
        self.assertNotIn("selected_text", first["task"])
        self.assertEqual(0, first["task"]["selected_text_ref"]["selected_field_index"])
        self.assertIn("exact_ref", first["task"]["anchor"])
        self.assertIn("exact_ref", first["task"]["anchor"]["fragments"][0])
        self.assertIn("exact_ref", first["task"]["anchor"]["quote"])
        self.assertIn("exact_ref", first["task"]["anchor"]["fragments"][0]["quote"])
        self.assertEqual("prefix", first["task"]["anchor"]["quote"]["prefix"])
        second = self.start(adapter, {**task, "id": "followup"})
        self.assertNotIn(text, json.dumps(second))

    def test_partial_selection_quote_keeps_literal_text(self):
        self.data["overview"]["sections"][0]["blocks"][0]["text_zh"] = "Full paragraph with a SHORT_SELECTED_EXCERPT inside."
        self.write_data()
        task = self.task()
        task["payload"]["selected_text"] = "SHORT_SELECTED_EXCERPT"
        task["payload"]["anchor"]["quote"] = {"exact": "SHORT_SELECTED_EXCERPT"}
        result = self.start(self.new_bridge(), task)["task"]
        self.assertEqual("SHORT_SELECTED_EXCERPT", result["selected_text"])
        self.assertEqual("SHORT_SELECTED_EXCERPT", result["anchor"]["quote"]["exact"])

    def test_reused_id_with_identical_content_does_not_guess_body_identity(self):
        block = self.data["overview"]["sections"][0]["blocks"][0]
        self.data["explanation"]["sections"] = [{"id": "other-section", "blocks": [deepcopy(block)]}]
        self.write_data()
        context = self.start(self.new_bridge())["context"]
        self.assertNotIn("reader_body_ref", context["selected_blocks"][0])
        self.assertEqual("正文", context["selected_blocks"][0]["text_zh"])
        self.assertIn("selected_block_ref", context["selected_fields"][0])

    def test_translation_selection_keeps_one_inline_copy(self):
        self.data["sections"][0]["blocks"] = [{"id": "p", "type": "paragraph", "text_zh": "TRANSLATION_SELECTION_ONLY"}]
        self.write_data()
        packet = self.start(self.new_bridge(), self.task("p", "translation"))
        context = packet["context"]
        self.assertEqual(1, json.dumps(packet).count("TRANSLATION_SELECTION_ONLY"))
        self.assertNotIn("reader_body_ref", context["selected_blocks"][0])
        self.assertIn("selected_block_ref", context["selected_fields"][0])

    def test_matching_translation_text_is_not_assumed_to_be_reader_body(self):
        self.data["sections"][0]["blocks"] = deepcopy(self.data["overview"]["sections"][0]["blocks"])
        self.write_data()
        context = self.start(self.new_bridge(), self.task("p", "translation"))["context"]
        self.assertNotIn("reader_body_ref", context["selected_blocks"][0])
        self.assertEqual("正文", context["selected_blocks"][0]["text_zh"])

    def test_unprojected_learning_field_remains_inline(self):
        block = self.data["overview"]["sections"][0]["blocks"][0]
        block["learning"] = {"mechanism": {"summary": "VISIBLE_RICH_FIELD_SENTINEL"}}
        self.write_data()
        task = self.task()
        task["payload"]["anchor"]["field"] = "learning.mechanism.summary"
        context = self.start(self.new_bridge(), task)["context"]
        self.assertEqual("VISIBLE_RICH_FIELD_SENTINEL", context["selected_fields"][0]["value"])

    def test_linked_formula_is_available_once_and_retains_identity(self):
        blocks = self.data["overview"]["sections"][0]["blocks"]
        blocks[0]["formula_ref"] = "eq"
        blocks.append({"id": "eq", "type": "formula", "latex": "UNIQUE_FORMULA_SENTINEL"})
        self.write_data()
        packet = self.start(self.new_bridge())
        self.assertEqual(1, json.dumps(packet).count("UNIQUE_FORMULA_SENTINEL"))
        selected = {b["id"]: b for b in packet["context"]["selected_blocks"]}
        self.assertEqual("eq", selected["eq"]["reader_body_ref"]["content_id"])


if __name__ == "__main__":
    unittest.main()
