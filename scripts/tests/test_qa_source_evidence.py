"""Local PDF verification and selected/explicit source image delivery regressions."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from bridge_client import build_context
from bridges.base import ConfirmedFailure
from bridges.codex_app_server import CodexAppServerBridge
from qa_source_evidence import confined, pdf_text_evidence, selected_images, requested_pdf_evidence

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


class FakeServer:
    def __init__(self):
        self.calls = []

    def rpc(self, method, params):
        self.calls.append((method, params))
        if method in ("thread/start", "thread/resume"):
            return {"thread": {"id": "thread-a", "model": "vision", "turns": []}, "model": "vision"}
        if method == "turn/start":
            return {"turn": {"id": "turn-a"}}
        raise AssertionError(method)


def adapter(vault, capability=True):
    bridge = object.__new__(CodexAppServerBridge)
    bridge.config = {"vault": str(vault)}
    bridge.server = FakeServer()
    bridge.active, bridge.sessions = set(), set()
    bridge.resumed = {}
    bridge.session_models = {"thread-a": "vision"}
    bridge.model_modalities = {"vision": ["text", "image"]} if capability else {}
    bridge.model_context_windows = {}
    bridge.session_instruction_tokens = {}
    return bridge


@unittest.skipIf(fitz is None, "PyMuPDF unavailable")
class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name)
        self.paper = self.vault / "papers" / "sample"
        self.paper.mkdir(parents=True)
        self.data = {"id": "sample", "content_version": 4,
            "overview": {"sections": [{"id": "o", "blocks": [{"id": "p", "type": "paragraph", "text_zh": "正文", "source_refs": []}]}]},
            "explanation": {"sections": []}, "sections": [{"id": "original", "blocks": []}]}
        self.write_data()
        self.write_pdf()

    def tearDown(self):
        self.temporary.cleanup()

    def write_data(self):
        (self.paper / "paper-data.js").write_text("window.PAPER_DATA=" + json.dumps(self.data, ensure_ascii=False) + ";", encoding="utf-8")

    def write_pdf(self, texts=("first body page", "second method page", "last appendix page")):
        with fitz.open() as pdf:
            for text in texts:
                page = pdf.new_page()
                page.insert_text((40, 60), text)
            pdf.save(self.paper / "replacement.pdf")
        (self.paper / "replacement.pdf").replace(self.paper / "source.pdf")

    def task(self, block_id="p", view="overview", question="为什么？"):
        return {"id": "task-a", "paper_id": "sample", "payload": {"user_note": question,
            "anchor": {"view": view, "content_id": block_id, "field": "text_zh"}}}

    def figure(self):
        src = "assets/figure2.png"
        path = self.paper / src
        path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(self.paper / "source.pdf") as pdf:
            path.write_bytes(pdf[1].get_pixmap().tobytes("png"))
        (self.paper / "asset-registry.json").write_text(json.dumps([{"src": src, "page": 2,
            "label": "Figure 2", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]), encoding="utf-8")
        block = {"id": "fig2", "type": "figure", "src": src, "page": 2,
            "caption_en": "Figure 2: The method.", "source_refs": [{"page": 2}], "text_zh": "图2"}
        self.data["sections"][0]["blocks"].append(block)
        self.write_data()
        return path

    def test_full_pdf_is_verified_locally_and_appendix_is_available_only_on_request(self):
        context = build_context(self.paper, self.task())
        self.assertEqual([1, 2, 3], context["source_coverage"]["covered_pages"])
        self.assertNotIn("text_pages", context["original_pdf"])
        source = requested_pdf_evidence(self.paper, {"pages": [3]}, context["original_pdf"]["pdf_sha256"])
        self.assertIn("last appendix", source["text_pages"][0]["text"])
        self.assertEqual(4, context["reader_snapshot"]["content_version"])
        self.assertFalse(context["context_capacity"]["truncated"])
        self.assertEqual("正文", context["selected_fields"][0]["value"])

    def test_hash_bound_cache_reused_and_rebuilt_for_changed_pdf(self):
        original = pdf_text_evidence(self.paper)
        with patch.object(fitz, "open", side_effect=AssertionError("verified cache should be reused")):
            self.assertEqual(original, pdf_text_evidence(self.paper))
        self.write_pdf(("different current paper",))
        current = pdf_text_evidence(self.paper)
        self.assertNotEqual(original["pdf_sha256"], current["pdf_sha256"])
        self.assertEqual([1], current["covered_pages"])
        self.assertIn("different current paper", current["text_pages"][0]["text"])

    def test_truncated_cache_is_not_accepted_as_complete(self):
        evidence = pdf_text_evidence(self.paper)
        cache = self.paper / "intermediate" / "qa-evidence" / ("text-" + evidence["pdf_sha256"] + ".json")
        value = json.loads(cache.read_text(encoding="utf-8"))
        value["text_pages"] = value["text_pages"][:1]
        cache.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(3, len(pdf_text_evidence(self.paper)["text_pages"]))

    def test_missing_pdf_has_explicit_unavailable_coverage_and_bad_pdf_fails(self):
        (self.paper / "source.pdf").unlink()
        self.assertEqual("unavailable", build_context(self.paper, self.task())["source_coverage"]["status"])
        (self.paper / "source.pdf").write_bytes(b"not a PDF")
        with self.assertRaises(Exception):
            build_context(self.paper, self.task())

    def test_anchored_figure2_original_pixels_delivered_with_simple_answer_schema(self):
        image = self.figure()
        task = self.task("fig2", "translation", "图里这一步为什么要保留？")
        context = build_context(self.paper, task)
        self.assertEqual("assets/figure2.png", context["selected_source_images"][0]["src"])
        bridge = adapter(self.vault)
        bridge.start("thread-a", task, context)
        params = bridge.server.calls[-1][1]
        self.assertEqual([{ "type": "localImage", "path": str(image.resolve())}], params["input"][1:])
        self.assertEqual({"answer", "sources"}, set(params["outputSchema"]["properties"]))

    def test_source_content_id_and_figure_label_automatically_link_original(self):
        self.figure()
        p = self.data["overview"]["sections"][0]["blocks"][0]
        for reference in ({"content_id": "fig2", "page": 2}, {"label": "Figure 2", "page": 2}):
            p["source_refs"] = [reference]
            self.write_data()
            context = build_context(self.paper, self.task())
            self.assertEqual(["assets/figure2.png"], [image["src"] for image in context["selected_source_images"]])

    def test_explicit_figure_question_uses_registered_original_without_disk_guess(self):
        self.figure()
        context = build_context(self.paper, self.task(question="请解释 Figure 2 的流程"))
        self.assertEqual(["assets/figure2.png"], [image["src"] for image in context["selected_source_images"]])

    def test_formula_gets_hash_registered_original_page_and_pixel_input(self):
        formula = {"id": "eq2", "type": "formula", "latex": "x+y=z", "label": "(2)", "text_zh": "式(2)", "source_refs": [{"page": 2}]}
        self.data["sections"][0]["blocks"].append(formula)
        self.write_data()
        task = self.task("eq2", "translation", "这里为什么要相加？")
        context = build_context(self.paper, task)
        image = context["selected_source_images"][0]
        self.assertEqual(2, image["page"])
        self.assertEqual(context["original_pdf"]["pdf_sha256"], image["pdf_sha256"])
        self.assertEqual(image["sha256"], hashlib.sha256((self.paper / image["src"]).read_bytes()).hexdigest())
        bridge = adapter(self.vault)
        bridge.start("thread-a", task, context)
        self.assertEqual("localImage", bridge.server.calls[-1][1]["input"][1]["type"])
        self.assertEqual(image, build_context(self.paper, task)["selected_source_images"][0])

    def test_nested_reader_formula_anchor_keeps_original_page_link(self):
        self.data["explanation"]["sections"] = [{"id": "method", "blocks": [{"id": "derivation", "type": "details",
            "summary": "必要推导", "blocks": [{"id": "nested-eq", "type": "formula", "latex": "x=1",
            "source_refs": [{"page": 2}]}]}]}]
        self.write_data()
        context = build_context(self.paper, self.task("nested-eq", "explanation"))
        self.assertEqual("nested-eq", context["selected_blocks"][0]["id"])
        self.assertEqual(2, context["selected_source_images"][0]["page"])

    def test_unknown_visual_modality_keeps_metadata_without_claiming_pixels(self):
        self.figure()
        task = self.task("fig2", "translation")
        bridge = adapter(self.vault, capability=False)
        context = build_context(self.paper, task)
        with patch("bridges.codex_app_server._verify_bitmap_file", side_effect=AssertionError("unknown model must not ingest pixels")):
            bridge.start("thread-a", task, context)
        params = bridge.server.calls[-1][1]
        self.assertEqual(1, len(params["input"]))
        self.assertIn("have NOT seen any image pixels", params["input"][0]["text"])

    def test_changed_registered_image_is_rejected_not_replaced_by_old_metadata(self):
        image = self.figure()
        image.write_bytes(b"changed bitmap")
        with self.assertRaisesRegex(ValueError, "changed or is missing"):
            build_context(self.paper, self.task("fig2", "translation"))

    def test_pdf_change_between_preparation_and_submission_fails_before_turn(self):
        task = self.task()
        context = build_context(self.paper, task)
        self.write_pdf(("new source",))
        bridge = adapter(self.vault)
        with self.assertRaisesRegex(ConfirmedFailure, "changed after context"):
            bridge.start("thread-a", task, context)
        self.assertEqual([], bridge.server.calls)

    def test_outside_registered_asset_and_invalid_physical_page_rejected(self):
        with self.assertRaises(ValueError):
            confined(self.paper, "../outside.png")
        with self.assertRaises(ValueError):
            confined(self.paper, "C:/outside.png")
        block = {"id": "eq", "type": "formula", "latex": "x=1", "source_refs": [{"page": 99}]}
        with self.assertRaisesRegex(ValueError, "outside the current PDF"):
            selected_images(self.paper, pdf_text_evidence(self.paper), [block], [block], {})

    def test_context_and_complete_turn_bounds_fail_without_truncation_or_rpc(self):
        self.data["overview"]["sections"][0]["blocks"][0]["text_zh"] = "汉" * 510000
        self.write_data()
        with self.assertRaisesRegex(ValueError, "no source pages were truncated"):
            build_context(self.paper, self.task())
        bridge = adapter(self.vault)
        task = self.task()
        task["payload"]["user_note"] = "x" * 1500001
        with self.assertRaisesRegex(ConfirmedFailure, "nothing was truncated"):
            bridge.start("thread-a", task, {})
        self.assertEqual([], bridge.server.calls)

    def test_known_model_window_is_checked_and_teacher_guide_really_loaded(self):
        bridge = adapter(self.vault)
        bridge.session(self.paper)
        instructions = bridge.server.calls[-1][1]["developerInstructions"]
        guide = (SCRIPTS.parent / "references" / "answer_worker_guide.md").read_text(encoding="utf-8-sig")
        self.assertIn(guide, instructions)
        self.assertIn("先直接回答当前疑问", instructions)
        self.assertIn("JSON 中反斜杠双写", instructions)
        self.assertIn("正确配对括号与数学环境", instructions)
        self.assertGreater(bridge.session_instruction_tokens["thread-a"], 0)
        for retired in ("plain_language_zh.md", "learning_support_guide.md", "visual_decision_guide.md", "anti_defensive_integration.md"):
            self.assertNotIn((SCRIPTS.parent / "references" / retired).read_text(encoding="utf-8-sig"), instructions)
        schema = json.loads((SCRIPTS.parent / "schemas" / "content-types.schema.json").read_text(encoding="utf-8-sig"))
        self.assertNotIn(json.dumps(schema, ensure_ascii=False, separators=(",", ":")), instructions)
        bridge.model_context_windows["vision"] = 100
        with self.assertRaisesRegex(ConfirmedFailure, "model window"):
            bridge.start("thread-a", self.task(), {})
        self.assertEqual(["thread/start"], [method for method, _params in bridge.server.calls])


if __name__ == "__main__":
    unittest.main()
