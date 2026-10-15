"""On-demand translation with a tiny real PDF and a fully local model stub."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from bridges.base import UnknownDelivery, ConfirmedFailure
from deploy_reader import load_paper_data
from reader_store import ReaderStore, StoreError
from serve_reader_v3 import ReaderServer
from translation_jobs import TranslationJobs, CodexTranslationRunner, CHECKS, BLOCK_SCHEMA, REVIEW_SCHEMA, assemble_chunk, check_review, prepare_chunks


def fixture(vault):
    import fitz
    paper = Path(vault) / "papers" / "tiny"
    paper.mkdir(parents=True)
    (paper / "reader.html").write_text("<!doctype html><title>Tiny</title>", encoding="utf-8")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 70), "A vertex split is the inverse of edge collapse.")
    document.save(paper / "source.pdf")
    document.close()
    data = {"schema_version": 3, "content_contract_version": "3.2", "content_version": "tiny-v1", "content_status": "ready",
        "id": "tiny", "title_en": "Tiny paper", "overview": {"sections": [{"id": "overview", "blocks": [
            {"id": "overview-p", "type": "paragraph", "text_zh": "概要", "source_refs": [{"page": 1}]}]}]},
        "explanation": {"sections": [{"id": "detail", "blocks": [{"id": "detail-p", "type": "paragraph", "text_zh": "详解"}]}]},
        "translation": {"status": "deferred"}, "sections": [], "content_review": {"reviewer": "existing-reviewer"}}
    save(paper, data)
    (paper / "annotations.json").write_text('{"annotations":[]}', encoding="utf-8")
    return paper, data


def save(paper, data):
    (paper / "paper-data.js").write_text("window.PAPER_DATA = " + json.dumps(data, ensure_ascii=False) + ";", encoding="utf-8")


def block(page):
    value = {key: [] if key.startswith(("headers_", "rows_")) or key in ("source_unit_ids", "source_pages") else "" for key in BLOCK_SCHEMA["properties"]}
    value.update(type="paragraph", text_en=" ".join(unit["text"] for unit in page["units"]),
        text_zh="顶点分裂是边塌缩的逆操作。", source_unit_ids=[unit["id"] for unit in page["units"]],
        source_pages=[page["page"]], crop=None)
    return value


class StubRunner:
    instances = []
    gate = None
    fail = None
    review_failure = False
    before_review = None

    def __init__(self, config):
        self.calls, self.config = [], config
        self.models = ["stub-host-default"]
        self.cancelled = threading.Event()
        type(self).instances.append(self)

    def call(self, phase, packet, images, stop, delivered):
        self.calls.append((phase, deepcopy(packet), images))
        delivered("stub-thread-" + phase, None)
        if self.gate:
            while not self.gate.wait(0.01):
                if stop.is_set() or self.cancelled.is_set():
                    raise RuntimeError("stub cancelled")
        if self.fail:
            raise self.fail
        delivered("stub-thread-" + phase, "stub-turn", confirmed=True)
        page = packet["source_pages"][0]
        if phase == "write":
            return {"sections": [{"title_en": "Source", "title_cn": "原文", "blocks": [block(page)]}], "omissions": [], "terms": []}
        if self.before_review:
            self.before_review()
        return {"approved": not self.review_failure, "issues": ["Chinese fidelity not verified"] if self.review_failure else [],
            "checked_pages": packet["target_pages"], "checked_unit_ids": [u["id"] for u in page["units"]],
            "checks": {key: True for key in CHECKS}, "candidate_decisions": [
                {"id": item["id"], "decision": "evidenced_false_positive", "evidence": "Verified original source unit is completely present in the supplied translated block."}
                for item in packet.get("coverage_candidates", [])]}

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.cancel()


class TranslationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paper, self.original = fixture(self.temp.name)
        self.store = ReaderStore(self.temp.name)
        StubRunner.instances, StubRunner.gate, StubRunner.fail, StubRunner.review_failure, StubRunner.before_review = [], None, None, False, None
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)

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
            if state["status"] not in ("queued", "running"):
                return state
            time.sleep(0.02)
        self.fail("translation did not reach a terminal state")

    def test_get_construction_and_restart_never_create_model(self):
        for _ in range(3):
            self.assertEqual("deferred", self.jobs.status("tiny")["status"])
        self.jobs.stop()
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
        self.assertEqual("deferred", self.jobs.status("tiny")["status"])
        self.assertEqual([], StubRunner.instances)
        self.assertEqual(self.original, load_paper_data(self.paper / "paper-data.js"))

    def test_explicit_generate_is_reviewed_and_merges_translation_only(self):
        annotations = (self.paper / "annotations.json").read_bytes()
        self.assertEqual("queued", self.start()["status"])
        state = self.terminal()
        self.assertEqual("ready", state["status"], state)
        self.assertEqual("tiny-v1", state["content_version"])
        self.assertEqual(64, len(state["content_hash"]))
        data = load_paper_data(self.paper / "paper-data.js")
        self.assertEqual(self.original["overview"], data["overview"])
        self.assertEqual(self.original["explanation"], data["explanation"])
        self.assertEqual(self.original["content_review"], data["content_review"])
        self.assertEqual(annotations, (self.paper / "annotations.json").read_bytes())
        self.assertEqual("ready", data["translation"]["status"])
        self.assertEqual("independent_codex_model", data["translation"]["review"]["reviewer"])
        self.assertEqual(["write", "review"], [call[0] for call in StubRunner.instances[0].calls])
        for _, packet, images in StubRunner.instances[0].calls:
            self.assertNotIn("overview", json.dumps(packet))
            self.assertTrue(Path(images[0]).is_file())

    def test_empty_container_titles_get_neutral_labels_without_source_rewriting(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        _, pages = prepare_chunks(self.paper, workspace)
        source_block = block(pages[0])
        payload = {"sections": [{"title_en": "", "title_cn": "", "blocks": [source_block]}], "omissions": [], "terms": []}
        before = deepcopy(payload)
        result = assemble_chunk(payload, pages[0], "translation-title-c0001", [])
        self.assertEqual("Source · p.1", result[0]["title_en"])
        self.assertEqual("原文 · p.1", result[0]["title_cn"])
        self.assertEqual(source_block["text_en"], result[0]["blocks"][0]["text_en"])
        self.assertEqual(source_block["text_zh"], result[0]["blocks"][0]["text_zh"])
        self.assertEqual(before, payload)

    def test_unknown_metadata_timeout_before_turn_submission_is_retryable(self):
        servers = []
        class BeforeTurnServer:
            phase = "thread/start"
            def __init__(self):
                self.calls = []
                servers.append(self)
            def rpc(self, method, params, **kwargs):
                self.calls.append(method)
                if method == self.phase:
                    raise UnknownDelivery(method + " timed out before any turn/start")
                if method == "thread/start":
                    return {"thread": {"id": "metadata-thread"}, "model": "host-default"}
                raise AssertionError("Unexpected RPC: " + method)
        class BeforeTurnRunner(CodexTranslationRunner):
            def __init__(self, config):
                super().__init__(config)
                self.server = BeforeTurnServer()
            def close(self):
                pass
        self.jobs.runner_factory = BeforeTurnRunner
        for phase in ("thread/start", "model/list"):
            with self.subTest(phase=phase):
                BeforeTurnServer.phase = phase
                self.start()  # Each new UUID is explicit intent, never an automatic retry.
                state = self.terminal()
                self.assertEqual("failed", state["status"])
                self.assertFalse(state["unknown_delivery"])
                self.assertTrue(state["retryable"])
                self.assertNotIn("turn/start", servers[-1].calls)
                self.assertFalse(self.jobs._load("tiny")["jobs"][state["job_id"]].get("delivery_pending"))
        self.assertEqual(self.original, load_paper_data(self.paper / "paper-data.js"))

    def test_published_ready_survives_one_state_save_failure_and_failed_records(self):
        original_save = self.jobs._save
        failed = []
        annotations = (self.paper / "annotations.json").read_bytes()
        def fail_first_ready(paper, record):
            if record["jobs"][record["current"]]["status"] == "ready" and not failed:
                failed.append(True)
                raise OSError("one transient ready state save failure")
            return original_save(paper, record)
        with patch.object(self.jobs, "_save", side_effect=fail_first_ready):
            self.start()
            with self.jobs.lock:
                owner = self.jobs.active.get("tiny")
            if owner:
                owner["thread"].join(timeout=5)
            self.assertTrue(failed)
            self.assertEqual("ready", self.jobs.status("tiny")["status"])
        published = load_paper_data(self.paper / "paper-data.js")
        self.assertEqual("ready", published["translation"]["status"])
        self.assertEqual(self.original["overview"], published["overview"])
        self.assertEqual(self.original["explanation"], published["explanation"])
        self.assertEqual(annotations, (self.paper / "annotations.json").read_bytes())
        # Older failures left a terminal failed record after publication. Both
        # reads and a restart must reconcile it without another host turn.
        for restart in (False, True):
            record = self.jobs._load("tiny")
            job = record["jobs"][record["current"]]
            job.update(status="failed", error="old state-write failure", unknown_delivery=True)
            original_save("tiny", record)
            if restart:
                self.jobs.stop()
                self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
            state = self.jobs.status("tiny")
            self.assertEqual("ready", state["status"])
            self.assertFalse(state["unknown_delivery"])
            self.assertNotIn("error", state)
        self.assertEqual("ready", self.start()["status"])
        self.assertEqual(1, len(StubRunner.instances))

    def test_legacy_list_and_algorithm_are_readable_without_rewriting_ids(self):
        for existing in (
            {"id": "legacy-list", "type": "list", "items": [{"text_en": "First step", "text_zh": "第一步"}]},
            {"id": "legacy-algorithm", "type": "algorithm", "algo_lines": ["return x"], "text_en": "Return x.", "text_zh": "返回x。"},
        ):
            with self.subTest(kind=existing["type"]):
                data = deepcopy(self.original)
                del data["translation"]
                data["sections"] = [{"id": "legacy-section", "blocks": [existing]}]
                save(self.paper, data)
                before = (self.paper / "paper-data.js").read_bytes()
                self.assertEqual("ready", self.jobs.status("tiny")["status"])
                self.assertEqual("ready", self.start()["status"])
                self.assertEqual(before, (self.paper / "paper-data.js").read_bytes())
                self.assertEqual([], StubRunner.instances)

    def test_nonempty_incomplete_translation_is_preserved_and_never_regenerated(self):
        data = deepcopy(self.original)
        del data["translation"]
        data["sections"] = [{"id": "keep-section", "blocks": [{"id": "keep-paragraph", "type": "paragraph", "text_en": "Existing source"}]}]
        save(self.paper, data)
        before = (self.paper / "paper-data.js").read_bytes()
        for state in (self.jobs.status("tiny"), self.start()):
            self.assertEqual("failed", state["status"])
            self.assertTrue(state["preserved"])
            self.assertFalse(state["retryable"])
        self.assertEqual(before, (self.paper / "paper-data.js").read_bytes())
        self.assertEqual([], StubRunner.instances)

    def test_parallel_clicks_and_request_replays_are_idempotent(self):
        StubRunner.gate = threading.Event()
        request_id = str(uuid.uuid4())
        first = self.start(request_id)
        aliases = [self.start(request_id), self.start()]
        self.assertTrue(all(result["job_id"] == first["job_id"] for result in aliases))
        deadline = time.monotonic() + 3
        while not StubRunner.instances and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(1, len(StubRunner.instances))
        StubRunner.gate.set()
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual(first["job_id"], self.start(request_id)["job_id"])
        self.assertEqual(first["job_id"], self.start()["job_id"])
        self.assertEqual(1, len(StubRunner.instances))

    def test_unknown_delivery_is_durable_and_blocks_new_generate(self):
        StubRunner.fail = UnknownDelivery("no confirmed response")
        request_id = str(uuid.uuid4())
        self.start(request_id)
        state = self.terminal()
        self.assertEqual("failed", state["status"])
        self.assertTrue(state["unknown_delivery"])
        self.assertEqual(state["job_id"], self.start(request_id)["job_id"])
        with self.assertRaises(StoreError) as conflict:
            self.start()
        self.assertEqual(409, conflict.exception.status)
        self.jobs.stop()
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
        self.assertTrue(self.jobs.status("tiny")["unknown_delivery"])
        self.assertEqual(1, len(StubRunner.instances))
        self.assertEqual(self.original, load_paper_data(self.paper / "paper-data.js"))

    def test_review_failure_preserves_reader_and_draft_without_automatic_retry(self):
        StubRunner.review_failure = True
        request_id = str(uuid.uuid4())
        self.start(request_id)
        state = self.terminal()
        self.assertEqual("failed", state["status"])
        self.assertIn("review did not pass", state["error"])
        self.assertFalse(state["unknown_delivery"])
        self.assertTrue(list(self.jobs.root.glob("tiny/*/translation-draft.js")))
        self.assertEqual(self.original, load_paper_data(self.paper / "paper-data.js"))
        self.start(request_id)
        self.assertEqual(1, len(StubRunner.instances))

    def test_shutdown_interrupts_and_restart_does_not_replay(self):
        StubRunner.gate = threading.Event()
        self.start()
        deadline = time.monotonic() + 3
        while not StubRunner.instances and time.monotonic() < deadline:
            time.sleep(0.01)
        self.jobs.stop()
        self.assertEqual("interrupted", self.jobs.status("tiny")["status"])
        self.assertTrue(StubRunner.instances[0].cancelled.is_set())
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
        self.assertEqual("interrupted", self.jobs.status("tiny")["status"])
        self.assertEqual(1, len(StubRunner.instances))

    def test_crashed_queued_record_becomes_interrupted_without_model(self):
        job_id, request_id = uuid.uuid4().hex, str(uuid.uuid4())
        self.jobs._save("tiny", {"schema_version": 1, "current": job_id, "requests": {request_id: job_id},
            "jobs": {job_id: {"job_id": job_id, "request_id": request_id, "status": "queued", "unknown_delivery": False}}})
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
        self.assertEqual("interrupted", self.jobs.status("tiny")["status"])
        self.assertEqual([], StubRunner.instances)

    def test_published_data_is_reconciled_after_state_write_crash(self):
        self.start()
        self.assertEqual("ready", self.terminal()["status"])
        self.jobs.stop()
        record = self.jobs._load("tiny")
        record["jobs"][record["current"]]["status"] = "running"
        self.jobs._save("tiny", record)
        self.jobs = TranslationJobs(self.store, runner_factory=StubRunner)
        self.assertEqual("ready", self.jobs.status("tiny")["status"])
        self.assertEqual(1, len(StubRunner.instances))

    def test_corrupt_durable_state_is_not_discarded(self):
        self.jobs._path("tiny").write_text("broken", encoding="utf-8")
        self.assertTrue(self.jobs.status("tiny")["unknown_delivery"])
        with self.assertRaises(StoreError):
            self.start()
        self.assertEqual([], StubRunner.instances)

    def test_existing_legacy_translation_is_available_without_model(self):
        data = deepcopy(self.original)
        del data["translation"]
        data["sections"] = [{"id": "old", "blocks": [{"id": "old-p", "type": "paragraph", "text_en": "Source", "text_zh": "原文"}]}]
        save(self.paper, data)
        self.assertEqual("ready", self.jobs.status("tiny")["status"])
        self.assertEqual("ready", self.start()["status"])
        self.assertEqual([], StubRunner.instances)
        self.assertEqual(data, load_paper_data(self.paper / "paper-data.js"))

    def test_latest_other_view_edits_survive_but_translation_edits_conflict(self):
        def edit_overview():
            data = load_paper_data(self.paper / "paper-data.js")
            data["overview"]["new_note"] = "concurrent edit"
            save(self.paper, data)
        StubRunner.before_review = staticmethod(edit_overview)
        self.start()
        self.assertEqual("ready", self.terminal()["status"])
        self.assertEqual("concurrent edit", load_paper_data(self.paper / "paper-data.js")["overview"]["new_note"])

    def test_translation_change_and_pdf_change_do_not_get_overwritten(self):
        def edit_translation():
            data = load_paper_data(self.paper / "paper-data.js")
            data["translation"]["user_note"] = "keep this"
            save(self.paper, data)
        StubRunner.before_review = staticmethod(edit_translation)
        self.start()
        state = self.terminal()
        self.assertEqual("failed", state["status"])
        self.assertIn("content changed", state["error"])
        self.assertEqual("keep this", load_paper_data(self.paper / "paper-data.js")["translation"]["user_note"])

    def test_source_pdf_change_prevents_publication(self):
        def change_pdf():
            with (self.paper / "source.pdf").open("ab") as output:
                output.write(b"\n% user edit\n")
        StubRunner.before_review = staticmethod(change_pdf)
        self.start()
        state = self.terminal()
        self.assertEqual("failed", state["status"])
        self.assertIn("source.pdf changed", state["error"])
        self.assertEqual(self.original, load_paper_data(self.paper / "paper-data.js"))

    def test_input_and_path_guards(self):
        for body in ({"request_id": "bad", "action": "generate"}, {"request_id": str(uuid.uuid4()), "action": "retry"},
                     {"request_id": str(uuid.uuid4()), "action": "generate", "model": "other"}):
            with self.assertRaises(StoreError):
                self.jobs.generate("tiny", body)
        with self.assertRaises(StoreError):
            self.jobs.status("../tiny")
        (self.paper / "source.pdf").unlink()
        with self.assertRaises(StoreError):
            self.start()
        self.assertEqual([], StubRunner.instances)

    def test_reuses_hash_verified_original_figure_without_recropping(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        _, pages = prepare_chunks(self.paper, workspace)
        asset = self.paper / "assets" / "figure.png"
        asset.parent.mkdir()
        asset.write_bytes(Path(pages[0]["image"]).read_bytes())
        from qa_source_evidence import digest_file
        (self.paper / "asset-registry.json").write_text(json.dumps([{"src": "assets/figure.png", "page": 1, "label": "Figure 1", "sha256": digest_file(asset)}]), encoding="utf-8")
        _, pages = prepare_chunks(self.paper, workspace)
        value = block(pages[0])
        value.update(type="figure", asset_src="assets/figure.png", caption_en="Figure 1. Source", caption_zh="图1：原图")
        crops = []
        result = assemble_chunk({"sections": [{"title_en": "Source", "title_cn": "原文", "blocks": [value]}], "omissions": [], "terms": []}, pages[0], "translation-test-c0001", crops)
        self.assertEqual("assets/figure.png", result[0]["blocks"][0]["src"])
        self.assertEqual([], crops)

    def test_formula_and_source_coverage_are_not_satisfied_by_screenshot(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        _, pages = prepare_chunks(self.paper, workspace)
        value = block(pages[0])
        value.update(type="formula", latex="")
        payload = {"sections": [{"title_en": "Source", "title_cn": "原文", "blocks": [value]}], "omissions": [], "terms": []}
        with self.assertRaises(ValueError):
            assemble_chunk(payload, pages[0], "translation-test-c0001", [])
        value.update(type="paragraph", source_unit_ids=[])
        with self.assertRaises(ValueError):
            assemble_chunk(payload, pages[0], "translation-test-c0001", [])


class TranslationHTTPTests(unittest.TestCase):
    def test_authenticated_post_is_the_only_trigger(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture(temporary)
            StubRunner.instances, StubRunner.gate, StubRunner.fail, StubRunner.review_failure, StubRunner.before_review = [], None, None, False, None
            server = ReaderServer(temporary, "tiny", bridge="none", translation_runner_factory=StubRunner)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method="GET", body=None, token=True, origin=None):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["X-Reader-Token"] = server.token
                if origin:
                    headers["Origin"] = origin
                req = Request(server.base_url + "/api/papers/tiny/translation", data=json.dumps(body).encode() if body else None, method=method, headers=headers)
                with urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            try:
                self.assertEqual("deferred", request()[1]["status"])
                self.assertEqual([], StubRunner.instances)
                body = {"action": "generate", "request_id": str(uuid.uuid4())}
                for token, origin in ((False, None), (True, "https://foreign.example")):
                    with self.assertRaises(HTTPError):
                        request("POST", body, token, origin)
                self.assertEqual([], StubRunner.instances)
                self.assertEqual(202, request("POST", body)[0])
                deadline = time.monotonic() + 8
                while request()[1]["status"] in ("queued", "running") and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual("ready", request()[1]["status"])
                self.assertEqual(200, request("POST", body)[0])
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.cleanup()


class TranslationWireTests(unittest.TestCase):
    def test_review_rpc_schema_enumerates_only_current_original_source_ids(self):
        invalid_review = {"approved": True, "issues": [], "checked_pages": [1],
            "checked_unit_ids": ["p0001-u0001", "p001-b001-u002"],
            "checks": {key: True for key in CHECKS}, "candidate_decisions": []}
        class Server:
            closed = False
            def __init__(self):
                self.turns = []
            def rpc(self, method, params, **kwargs):
                if method == "thread/start":
                    return {"thread": {"id": "source-enum-thread"}}
                if method == "turn/start":
                    self.turns.append(deepcopy(params))
                    return {"turn": {"id": "source-enum-turn"}}
                raise AssertionError(method)
            def drain_turn(self, *_args):
                return [{"method": "turn/completed", "params": {"turn": {"status": "completed", "items": [
                    {"type": "agentMessage", "phase": "final_answer", "text": json.dumps(invalid_review)}]}}}]
        runner = CodexTranslationRunner({"image_input_supported": True})
        runner.server = Server()
        for number, ids in ((1, ["p0001-u0001"]), (2, ["p0002-u0001"]), (3, [])):
            page = {"page": number, "units": [{"id": unit_id} for unit_id in ids]}
            packet = {"context_directory": ".", "call_id": str(uuid.uuid4()), "target_pages": [number], "source_pages": [page]}
            result = runner.call("review", packet, [], threading.Event(), lambda *args, **kwargs: None)
            schema = runner.server.turns[-1]["outputSchema"]["properties"]
            self.assertEqual([number], schema["checked_pages"]["items"]["enum"])
            self.assertEqual(len(ids), schema["checked_unit_ids"]["minItems"])
            self.assertEqual(len(ids), schema["checked_unit_ids"]["maxItems"])
            self.assertIn("never use translated section/block IDs", schema["checked_unit_ids"]["description"])
            if ids:
                self.assertEqual(ids, schema["checked_unit_ids"]["items"]["enum"])
            else:
                self.assertNotIn("enum", schema["checked_unit_ids"]["items"])
            self.assertNotIn("enum", schema["issues"]["items"])
            if number == 1:
                # A malformed host response is still rejected in full; its bad
                # IDs are never filtered into an apparently passing review.
                with self.assertRaisesRegex(ValueError, "all target pages/source units"):
                    check_review(result, page, [])
        self.assertEqual(["p0001-u0001"], runner.server.turns[0]["outputSchema"]["properties"]["checked_unit_ids"]["items"]["enum"])
        self.assertNotIn("enum", REVIEW_SCHEMA["properties"]["checked_unit_ids"]["items"])

    def test_host_defaults_independent_context_and_separate_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            paper, _ = fixture(temporary)
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            _, pages = prepare_chunks(paper, workspace)
            class Server:
                def __init__(self):
                    self.calls = []
                    self.changed = threading.Condition()
                    self.closed = False
                def rpc(self, method, params, **kwargs):
                    self.calls.append((method, deepcopy(params)))
                    if method == "thread/start":
                        return {"thread": {"id": "translation-thread"}, "model": "host-selected"}
                    if method == "model/list":
                        return {"data": [{"id": "host-selected", "inputModalities": ["text", "image"]}]}
                    if method == "turn/start":
                        return {"turn": {"id": "translation-turn"}}
                    raise AssertionError(method)
                def drain_turn(self, *_args):
                    return [{"method": "turn/completed", "params": {"turn": {"status": "completed", "items": [
                        {"type": "agentMessage", "phase": "final_answer", "text": '{"sections":[],"omissions":[],"terms":[]}'}]}}}]
            runner = CodexTranslationRunner({})
            runner.server = Server()
            deliveries = []
            for phase in ("write", "review"):
                runner.call(phase, {"context_directory": str(workspace), "call_id": str(uuid.uuid4()), "source": "original paper"},
                    [pages[0]["image"]], threading.Event(), lambda *args, **kwargs: deliveries.append((args, kwargs)))
            threads = [params for method, params in runner.server.calls if method == "thread/start"]
            turns = [params for method, params in runner.server.calls if method == "turn/start"]
            self.assertEqual(2, len(threads))
            for params in threads:
                self.assertEqual("never", params["approvalPolicy"])
                self.assertEqual("read-only", params["sandbox"])
                self.assertNotIn("model", params)
                self.assertNotIn("effort", params)
                self.assertIn("source data", params["developerInstructions"])
                self.assertNotIn("answer_worker_guide", params["developerInstructions"])
                self.assertFalse(params["config"]["features.shell_tool"])
            for params in turns:
                self.assertNotIn("model", params)
                self.assertNotIn("effort", params)
                self.assertEqual("localImage", params["input"][1]["type"])
                self.assertNotIn("answer", params["outputSchema"]["properties"])
            self.assertNotEqual(turns[0]["outputSchema"], turns[1]["outputSchema"])
            self.assertEqual(6, len(deliveries))

    def test_confirmed_turn_rejection_does_not_become_unknown_delivery(self):
        class Server:
            def rpc(self, method, params, **kwargs):
                if method == "thread/start":
                    return {"thread": {"id": "thread"}}
                raise RuntimeError("turn/start rejected by host")
        runner = CodexTranslationRunner({"image_input_supported": True})
        runner.server = Server()
        with self.assertRaises(ConfirmedFailure):
            runner.call("write", {"context_directory": ".", "call_id": "test"}, [], threading.Event(), lambda *args: None)


if __name__ == "__main__":
    unittest.main()
