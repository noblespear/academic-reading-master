"""Translation reuse uses local stubs, real tiny PDF pixels, and explicit jobs."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridges.base import UnknownDelivery
from model_profiles import save_profiles
from reader_store import ReaderStore, StoreError
import translation_jobs as translation
from test_translation_jobs import block, fixture


def reencode_png(path):
    pixels = translation._fitz().Pixmap(str(path))
    pixels.set_dpi(144, 144)
    pixels.save(str(path))


class PageRunner:
    calls = []
    instances = []
    fail_reviews = set()
    unknown_at = None
    terms = {}

    def __init__(self, config):
        self.config = dict(config)
        self.models = [config.get("model", "host-default")]
        type(self).instances.append(self)

    def call(self, phase, packet, images, stop, delivered):
        page = packet["source_pages"][0]
        number = page["page"]
        type(self).calls.append((phase, number, deepcopy(packet)))
        delivered("stub-" + phase, None)
        if type(self).unknown_at == (phase, number):
            raise UnknownDelivery("a submitted turn has no terminal confirmation")
        delivered("stub-" + phase, "confirmed", confirmed=True)
        if phase == "write":
            return {"sections": [{"title_en": "", "title_cn": "", "blocks": [block(page)]}],
                "omissions": [], "terms": deepcopy(type(self).terms.get(number, []))}
        failed = number in type(self).fail_reviews
        type(self).fail_reviews.discard(number)
        return {"approved": not failed, "issues": ["Chinese fidelity failed"] if failed else [],
            "checked_pages": [number], "checked_unit_ids": [unit["id"] for unit in page["units"]],
            "checks": {key: True for key in translation.CHECKS}, "candidate_decisions": [
                {"id": candidate["id"], "decision": "evidenced_false_positive",
                    "evidence": "The original page text is completely present in the identified translated block."}
                for candidate in packet["coverage_candidates"]]}

    def cancel(self):
        pass

    def close(self):
        pass


class TranslationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paper, _ = fixture(self.temp.name)
        import fitz
        with fitz.open() as document:
            for number in range(1, 4):
                page = document.new_page()
                page.insert_text((50, 70), f"Page {number}. A vertex split is the inverse of edge collapse.")
            document.save(self.paper / "source.pdf")
        self.store = ReaderStore(self.temp.name)
        PageRunner.calls, PageRunner.instances = [], []
        PageRunner.fail_reviews, PageRunner.unknown_at, PageRunner.terms = set(), None, {}
        self.jobs = translation.TranslationJobs(self.store,
            {"model": "legacy-global", "effort": "low", "codex_bin": "preserved-transport", "token": "private-transport"},
            runner_factory=PageRunner)

    def tearDown(self):
        self.jobs.stop()
        self.store.close()
        self.temp.cleanup()

    def start(self, request_id=None):
        return self.jobs.generate("tiny", {"request_id": request_id or str(uuid.uuid4()), "action": "generate"})

    def terminal(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = self.jobs.status("tiny")
            if state["status"] not in translation.ACTIVE:
                with self.jobs.lock:
                    owner = self.jobs.active.get("tiny")
                if owner:
                    owner["thread"].join(timeout=3)
                return state
            time.sleep(.01)
        self.fail("job did not finish")

    def first_failure(self):
        PageRunner.fail_reviews = {2}
        self.start()
        state = self.terminal()
        self.assertEqual("failed", state["status"], state)
        self.assertFalse(state["unknown_delivery"])
        self.assertEqual([("write", 1), ("write", 2), ("write", 3), ("review", 1), ("review", 2)], self.call_pages())
        return state

    def call_pages(self):
        return [(phase, page) for phase, page, _ in PageRunner.calls]

    def checkpoint_files(self, phase):
        return sorted((self.jobs.root / "tiny" / "checkpoints").glob(phase + "-*.json"))

    def test_explicit_retry_reuses_three_writers_and_already_approved_review(self):
        failed = self.first_failure()
        self.assertEqual(3, len(self.checkpoint_files("write")))
        self.assertEqual(1, len(self.checkpoint_files("review")))
        for _ in range(3):
            self.jobs.status("tiny")
        self.start(failed["request_id"])
        self.assertEqual(5, len(PageRunner.calls))
        retried = self.start()
        self.assertNotEqual(failed["job_id"], retried["job_id"])
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual([("review", 2), ("review", 3)], self.call_pages()[5:])
        self.assertEqual(3, len(self.checkpoint_files("review")))

    def assert_all_writers_repeated(self):
        self.start()
        state = self.terminal()
        self.assertEqual("ready", state["status"], state)
        self.assertEqual([("write", 1), ("write", 2), ("write", 3)], self.call_pages()[5:8])
        self.assertEqual([("review", 1), ("review", 2), ("review", 3)], self.call_pages()[8:])

    def test_pdf_content_hash_change_invalidates_completed_pages(self):
        self.first_failure()
        with (self.paper / "source.pdf").open("ab") as output:
            output.write(b"\n% a changed PDF with unchanged rendered text\n")
        self.assert_all_writers_repeated()

    def test_translation_rule_change_invalidates_completed_pages(self):
        guides = Path(self.temp.name) / "guides"
        guides.mkdir()
        for name in ("translation_rules.md", "math_rules.md"):
            (guides / name).write_text((translation.GUIDES / name).read_text(encoding="utf-8-sig"), encoding="utf-8")
        with patch.object(translation, "GUIDES", guides):
            self.first_failure()
            with (guides / "translation_rules.md").open("a", encoding="utf-8") as output:
                output.write("\nPreserve the revised terminology exactly.\n")
            self.assert_all_writers_repeated()

    def test_schema_change_invalidates_completed_pages(self):
        self.first_failure()
        schema = deepcopy(translation.WRITE_SCHEMA)
        schema["description"] = "Changed writer output contract"
        with patch.object(translation, "WRITE_SCHEMA", schema):
            self.assert_all_writers_repeated()

    def test_review_schema_change_reuses_writers_and_invalidates_approved_review(self):
        self.first_failure()
        schema = deepcopy(translation.REVIEW_SCHEMA)
        schema["description"] = "Changed independent review output contract"
        with patch.object(translation, "REVIEW_SCHEMA", schema):
            self.start()
            self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual([("review", 1), ("review", 2), ("review", 3)], self.call_pages()[5:])

    def test_verified_asset_change_invalidates_its_page_without_repeating_other_writers(self):
        workspace = Path(self.temp.name) / "asset-preparation"
        workspace.mkdir()
        _, pages = translation.prepare_chunks(self.paper, workspace)
        asset = self.paper / "assets" / "original.png"
        asset.parent.mkdir()
        asset.write_bytes(Path(pages[0]["image"]).read_bytes())
        registry = self.paper / "asset-registry.json"
        def register():
            registry.write_text(json.dumps([{"src": "assets/original.png", "page": 1,
                "sha256": translation.digest_file(asset)}]), encoding="utf-8")
        register()
        self.first_failure()
        reencode_png(asset)
        register()
        self.start()
        state = self.terminal()
        self.assertEqual("ready", state["status"], state)
        self.assertEqual([("write", 1), ("review", 1), ("review", 2), ("review", 3)], self.call_pages()[5:])

    def test_prepared_page_image_hash_change_invalidates_its_page(self):
        self.first_failure()
        original_prepare = translation.prepare_chunks
        def changed_page_pixels(paper, workspace):
            pdf_hash, pages = original_prepare(paper, workspace)
            path = Path(pages[0]["image"])
            reencode_png(path)
            pages[0]["image_sha256"] = translation.digest_file(path)
            return pdf_hash, pages
        with patch.object(translation, "prepare_chunks", side_effect=changed_page_pixels):
            self.start()
            self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual([("write", 1), ("review", 1), ("review", 2), ("review", 3)], self.call_pages()[5:])

    def test_translation_model_change_invalidates_completed_pages_but_qa_change_does_not(self):
        save_profiles(self.store.vault, {"translation": {"model": "translation-a", "effort": "medium"},
            "qa": {"model": "qa-a", "effort": "high"}})
        self.first_failure()
        save_profiles(self.store.vault, {"qa": {"model": "qa-b", "effort": "low"}})
        # A fresh explicit job after a QA-only settings change reuses completed
        # translation pages, and can still fail its outstanding review normally.
        PageRunner.fail_reviews = {2}
        self.start()
        self.assertEqual("failed", self.terminal()["status"])
        self.assertEqual([("review", 2)], self.call_pages()[5:])
        save_profiles(self.store.vault, {"translation": {"model": "translation-b", "effort": "medium"}})
        offset = len(PageRunner.calls)
        self.start()
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual([("write", 1), ("write", 2), ("write", 3),
            ("review", 1), ("review", 2), ("review", 3)], self.call_pages()[offset:])

    def test_upstream_terminology_change_invalidates_later_writer_dependencies(self):
        PageRunner.terms = {1: [{"en": "vertex split", "zh": "顶点分裂"}]}
        self.first_failure()
        for path in self.checkpoint_files("write"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["value"]["terms"]:
                path.unlink()  # The first page must now be explicitly generated again.
        PageRunner.terms = {1: [{"en": "vertex split", "zh": "顶点拆分"}]}
        self.assert_all_writers_repeated()
        later = [packet for phase, page, packet in PageRunner.calls[5:] if phase == "write" and page > 1]
        self.assertTrue(all(packet["terminology"] == {"vertex split": "顶点拆分"} for packet in later))

    def test_cached_writer_is_locally_validated_again_even_with_recomputed_checksum(self):
        self.first_failure()
        for path in self.checkpoint_files("write"):
            record = json.loads(path.read_text(encoding="utf-8"))
            value = record["value"]["sections"][0]["blocks"][0]
            if value["source_pages"] == [1]:
                value["source_unit_ids"] = []
                record["sha256"] = translation._fingerprint(record["value"])
                path.write_text(json.dumps(record), encoding="utf-8")
        self.start()
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual([("write", 1), ("review", 2), ("review", 3)], self.call_pages()[5:])

    def test_unknown_delivery_blocks_new_jobs_even_with_valid_writer_checkpoints(self):
        PageRunner.unknown_at = ("review", 2)
        requested = self.start()
        state = self.terminal()
        self.assertTrue(state["unknown_delivery"])
        self.assertEqual(3, len(self.checkpoint_files("write")))
        self.jobs.status("tiny")
        self.start(requested["request_id"])
        with self.assertRaises(StoreError):
            self.start()
        count = len(PageRunner.calls)
        self.jobs.stop()
        self.jobs = translation.TranslationJobs(self.store, runner_factory=PageRunner)
        self.assertTrue(self.jobs.status("tiny")["unknown_delivery"])
        with self.assertRaises(StoreError):
            self.start()
        self.assertEqual(count, len(PageRunner.calls))

    def test_model_snapshot_is_frozen_at_acceptance_and_contains_no_transport_secrets(self):
        save_profiles(self.store.vault, {"translation": {"model": "translation-a", "effort": "medium"},
            "qa": {"model": "qa-a", "effort": "high"}})
        resume = threading.Event()
        original_run = self.jobs._run
        def paused_run(paper, job_id):
            resume.wait(3)
            original_run(paper, job_id)
        self.jobs._run = paused_run
        try:
            requested = self.start()
            save_profiles(self.store.vault, {"translation": {"model": "translation-b", "effort": "high"}})
            self.jobs.status("tiny")
            self.assertEqual([], PageRunner.instances)
            job = self.jobs._load("tiny")["jobs"][requested["job_id"]]
            self.assertEqual({"model": "translation-a", "effort": "medium"}, job["model_profile"])
            self.assertNotIn("private-transport", json.dumps(job))
        finally:
            resume.set()
        self.assertEqual("ready", self.terminal()["status"])
        config = PageRunner.instances[0].config
        self.assertEqual("translation-a", config["model"])
        self.assertEqual("medium", config["effort"])
        self.assertEqual("preserved-transport", config["codex_bin"])

    def test_explicit_inheritance_removes_global_overrides_from_runner(self):
        save_profiles(self.store.vault, {"translation": {"model": "", "effort": ""},
            "qa": {"model": "qa-only", "effort": "high"}})
        requested = self.start()
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual({}, self.jobs._load("tiny")["jobs"][requested["job_id"]]["model_profile"])
        self.assertNotIn("model", PageRunner.instances[0].config)
        self.assertNotIn("effort", PageRunner.instances[0].config)
        self.assertEqual("preserved-transport", PageRunner.instances[0].config["codex_bin"])


class TranslationPacketReuseTests(unittest.TestCase):
    def test_review_content_appears_once_with_complete_unit_and_omission_mapping(self):
        page = {"page": 1, "width": 600, "height": 800,
            "units": [{"id": "p0001-u" + str(n), "text": "Original source " + str(n)} for n in range(1, 6)],
            "assets": [{"src": "assets/original.png", "page": 1}]}
        blocks = []
        sentinels = []
        for index, kind in enumerate(("paragraph", "formula", "table", "figure"), 1):
            value = block(page)
            value.update(type=kind, source_unit_ids=[page["units"][index - 1]["id"]],
                text_en="", text_zh="", caption_en="", caption_zh="")
            if kind == "paragraph":
                value.update(text_en="BODY_EN_SENTINEL", text_zh="BODY_ZH_SENTINEL")
                sentinels.extend((value["text_en"], value["text_zh"]))
            elif kind == "formula":
                value["latex"] = "x=314159"
                sentinels.append(value["latex"])
            else:
                value.update(caption_en=kind.upper() + "_CAPTION_EN_SENTINEL",
                    caption_zh=kind.upper() + "_CAPTION_ZH_SENTINEL")
                sentinels.extend((value["caption_en"], value["caption_zh"]))
                if kind == "table":
                    value.update(headers_en=["TABLE_HEADER_EN"], headers_zh=["TABLE_HEADER_ZH"],
                        rows_en=[["12345"]], rows_zh=[["12345"]])
                    sentinels.extend(("TABLE_HEADER_EN", "TABLE_HEADER_ZH"))
                else:
                    value["asset_src"] = "assets/original.png"
            blocks.append(value)
        writer = {"sections": [{"title_en": "Source", "title_cn": "原文", "blocks": blocks}],
            "omissions": [{"unit_id": page["units"][-1]["id"], "reason": "running page number"}], "terms": []}
        sections = translation.assemble_chunk(writer, page, "translation-packet-c0001", [])
        accounting = translation._source_accounting(writer, sections)
        encoded = json.dumps({"translation_sections": sections, "writer_source_accounting": accounting}, ensure_ascii=False)
        for sentinel in sentinels:
            self.assertEqual(1, encoded.count(sentinel), sentinel)
        self.assertNotIn("sections", accounting)
        self.assertEqual({item["id"] for item in sections[0]["blocks"]}, set(accounting["blocks"]))
        accounted = {unit for entry in accounting["blocks"].values() for unit in entry["source_unit_ids"]}
        accounted.update(item["unit_id"] for item in accounting["omissions"])
        self.assertEqual({unit["id"] for unit in page["units"]}, accounted)
        self.assertTrue(all(entry["source_pages"] == [1] for entry in accounting["blocks"].values()))
        self.assertEqual("assets/original.png", list(accounting["blocks"].values())[-1]["asset_src"])

    def test_wire_rules_and_sources_precede_role_and_random_call_id(self):
        class Server:
            closed = False
            def __init__(self):
                self.threads, self.turns = [], []
            def rpc(self, method, params, **kwargs):
                if method == "thread/start":
                    self.threads.append(params)
                    return {"thread": {"id": "stub-thread"}}
                if method == "turn/start":
                    self.turns.append(params)
                    return {"turn": {"id": "stub-turn"}}
                raise AssertionError(method)
            def drain_turn(self, *_args):
                return [{"method": "turn/completed", "params": {"turn": {"status": "completed", "items": [
                    {"type": "agentMessage", "text": "{}"}]}}}]
        runner = translation.CodexTranslationRunner({"image_input_supported": True})
        runner.server = Server()
        for phase in ("write", "review"):
            packet = {"call_id": uuid.uuid4().hex, "context_directory": ".", "source_pages": [{"page": 1, "units": []}],
                "target_pages": [1], "operation": phase, "instructions": "bounded dynamic task"}
            runner.call(phase, packet, [], threading.Event(), lambda *args, **kwargs: None)
        instructions = [params["developerInstructions"] for params in runner.server.threads]
        self.assertEqual(instructions[0].rsplit("\n", 1)[0], instructions[1].rsplit("\n", 1)[0])
        self.assertTrue(all(value.startswith(translation.COMMON_INSTRUCTIONS) for value in instructions))
        for params in runner.server.turns:
            keys = list(json.loads(params["input"][0]["text"]))
            self.assertLess(keys.index("source_pages"), keys.index("operation"))
            self.assertEqual("call_id", keys[-1])


if __name__ == "__main__":
    unittest.main()
