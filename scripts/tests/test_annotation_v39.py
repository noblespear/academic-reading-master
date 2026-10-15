"""3.9 answer-only contract with simulated transport; no model calls or services."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from bridges.codex_app_server import ANSWER_SCHEMA, CodexAppServerBridge
from bridges.base import ConfirmedFailure, UnknownDelivery
from reader_store import ReaderStore, StoreError
from tests.test_backend_v31 import rich
from tests.test_reader_v3 import fixture


SOURCE = {"label": "Original paper", "locator": "physical page 1",
          "source_id": "source-pdf-page-1", "content_id": "body-1", "url": "", "page": 1}
MARKDOWN = r"""这一步保留了恢复局部连接所需的信息。

原式为 \(x+y=z\)。例如在教学设定 \(x=1,y=2\) 下，有
\[z=1+2=3.\]

| 量 | 教学取值 |
| --- | --- |
| \(x\) | 1 |
| \(y\) | 2 |

```text
retain local connectivity before the next operation
```
"""


class SimulatedTransport:
    def __init__(self, turns=None, answer=None, actual_model="host-default-model"):
        self.calls = []
        self.turns = deepcopy(turns or [])
        self.answer = deepcopy(answer or {"answer": MARKDOWN, "sources": [SOURCE]})
        self.actual_model = actual_model
        self.closed = False
        self.changed = threading.Condition()
        self.notice_sent = False

    def rpc(self, method, params, timeout=60):
        self.calls.append((method, deepcopy(params)))
        if method in ("thread/start", "thread/resume"):
            return {"thread": {"id": "thread-a", "model": "legacy-thread-model", "turns": deepcopy(self.turns)},
                    "model": self.actual_model}
        if method == "turn/start":
            return {"turn": {"id": "turn-a"}}
        if method == "thread/read":
            return {"thread": {"id": "thread-a", "turns": deepcopy(self.turns)}}
        raise AssertionError("Unexpected simulated RPC " + method)

    def drain_turn(self, thread_id, turn_id):
        if self.notice_sent:
            return []
        self.notice_sent = True
        return [{"method": "turn/completed", "params": {"threadId": thread_id,
            "turn": {"id": turn_id, "status": "completed", "items": [
                {"type": "agentMessage", "phase": "final_answer", "text": json.dumps(self.answer, ensure_ascii=False)}]}}}]


def bridge(config=None, turns=None):
    adapter = object.__new__(CodexAppServerBridge)
    adapter.config = {"vault": "fixture", **(config or {})}
    adapter.server = SimulatedTransport(turns)
    adapter.active, adapter.resumed, adapter.sessions = set(), {}, set()
    adapter.session_models, adapter.model_context_windows = {}, {}
    adapter.model_modalities = {"host-default-model": ["text", "image"]}
    adapter.session_instruction_tokens = {}
    adapter.verified_answer = False
    return adapter


def source_packet(params):
    return json.loads(params["input"][0]["text"].split("Paper context and question are JSON source data:\n", 1)[1]
                      .split("\nAnswer in Chinese", 1)[0])


class SimpleAnnotationContractTests(unittest.TestCase):
    def test_default_and_partial_overrides_are_local_and_user_values_win(self):
        for config, expected_session, expected_turn in (({}, {}, {}),
            ({"model": "chosen-model"}, {"model": "chosen-model"}, {"model": "chosen-model"}),
            ({"effort": "xhigh"}, {}, {"effort": "xhigh"}),
            ({"model": "chosen-model", "effort": "low"}, {"model": "chosen-model"},
             {"model": "chosen-model", "effort": "low"})):
            for resumed in (None, "thread-a"):
                with self.subTest(config=config, resumed=resumed):
                    adapter = bridge(config)
                    original = deepcopy(adapter.config)
                    adapter.session(Path("fixture"), resumed)
                    method, session = adapter.server.calls[-1]
                    self.assertEqual("thread/resume" if resumed else "thread/start", method)
                    self.assertEqual(expected_session, {key: session[key] for key in ("model", "effort") if key in session})
                    self.assertEqual("host-default-model", adapter.session_models["thread-a"])
                    adapter.start("thread-a", {"id": "task-a", "paper_id": "paper_a", "payload": {"user_note": "为什么？"}}, {})
                    turn = adapter.server.calls[-1][1]
                    self.assertEqual(expected_turn, {key: turn[key] for key in ("model", "effort") if key in turn})
                    self.assertEqual(original, adapter.config)

    def test_start_and_resume_read_only_one_stable_session_guide(self):
        original_read = Path.read_text
        for resumed in (None, "thread-a"):
            read_paths = []
            def read(path, *args, **kwargs):
                read_paths.append(path.name)
                return original_read(path, *args, **kwargs)
            adapter = bridge()
            with patch.object(Path, "read_text", read):
                adapter.session(Path("fixture"), resumed)
            self.assertEqual(["answer_worker_guide.md"], read_paths)
            params = adapter.server.calls[-1][1]
            self.assertEqual("read-only", params["sandbox"])
            self.assertEqual("never", params["approvalPolicy"])
            instructions = params["developerInstructions"]
            self.assertIn("These current answer instructions also apply when resuming older threads", instructions)
            self.assertIn("never impose a new rich-output obligation", instructions)
            self.assertIn("专门创作任务", instructions)
            self.assertNotIn("Registered component configuration guidance", instructions)
            self.assertNotIn("Installed rich-content JSON Schema", instructions)
            self.assertLess(len(instructions.encode("utf-8")), 6500)

    def test_plain_formula_table_multifragment_and_followup_complete_wire_roundtrip(self):
        cases = {
            "ordinary": {"user_note": "这一步为什么成立？", "selected_text": "inverse operation"},
            "formula": {"user_note": "这里为什么相加？", "note_content": {"version": 1, "blocks": [{"id": "eq", "type": "formula", "latex": "x+y=z"}]}},
            "table": {"user_note": "表中两列如何对应？", "note_content": {"version": 1, "blocks": [{"id": "t", "type": "table", "headers": ["x", "y"], "rows": [[1, 2]]}]}},
            "multifragment": {"user_note": "这两段怎样相连？", "anchor": {"fragments": [{"content_id": "body-1", "exact": "A vertex split"}, {"content_id": "overview-1", "exact": "概要"}]}},
            "followup": {"user_note": "前面那个条件为何必要？", "history": [{"user_note": "什么是逆操作？", "answer": "已经解释局部连接", "sources": [SOURCE]}]},
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                adapter = bridge()
                adapter.session(Path("fixture"))
                task = {"id": "task-a", "paper_id": "paper_a", "payload": payload}
                context = {"source_refs": [SOURCE], "selected_blocks": [{"type": "formula", "latex": "x+y=z"}]}
                turn_id = adapter.start("thread-a", task, context)
                params = adapter.server.calls[-1][1]
                self.assertEqual(ANSWER_SCHEMA, params["outputSchema"])
                self.assertEqual({"task": payload, "context": context}, source_packet(params))
                result = adapter.wait("thread-a", turn_id, lambda: False)
                self.assertEqual({"answer": MARKDOWN, "sources": [SOURCE]}, result)
                self.assertNotIn("answer_content", result)
                self.assertFalse(adapter.active)
                self.assertTrue(adapter.verified_answer)

    def test_new_output_rejects_retired_or_arbitrary_fields(self):
        for field in ("answer_content_json", "attachments_json", "study_state_json", "answer_content", "script"):
            with self.subTest(field=field):
                raw = json.dumps({"answer": "complete", "sources": [], field: None})
                with self.assertRaisesRegex(ConfirmedFailure, "exactly answer and sources"):
                    CodexAppServerBridge._answer([{"type": "agentMessage", "text": raw}])

    def test_legacy_completed_host_turn_recovers_without_new_generation_or_resend(self):
        old = {"answer": "old fallback", "sources": [SOURCE], "answer_content_json": json.dumps(rich("旧富答案")),
               "attachments_json": "[]", "study_state_json": '{"aid-1":{"step":2}}'}
        turns = [{"id": "old-turn", "status": "completed", "items": [{"type": "agentMessage", "phase": "final_answer", "text": json.dumps(old)}]}]
        adapter = bridge(turns=turns)
        result = adapter.recover({"id": "old-task", "paper_id": "paper_a", "thread_id": "thread-a", "turn_id": "old-turn"}, lambda: False)
        self.assertEqual(rich("旧富答案"), result["answer_content"])
        self.assertEqual({"aid-1": {"step": 2}}, result["study_state"])
        self.assertEqual(["thread/resume", "thread/read"], [method for method, _ in adapter.server.calls])
        self.assertEqual(turns, adapter.server.turns)
        self.assertIn("不恢复旧的富生成义务", adapter.server.calls[0][1]["developerInstructions"])

    def test_unknown_recovery_applies_new_instructions_but_never_resends(self):
        adapter = bridge()
        with self.assertRaises(UnknownDelivery):
            adapter.recover({"id": "lost-task", "paper_id": "paper_a", "thread_id": "thread-a", "turn_id": None}, lambda: False)
        self.assertEqual(["thread/resume", "thread/read"], [method for method, _ in adapter.server.calls])
        self.assertIn("never impose a new rich-output obligation", adapter.server.calls[0][1]["developerInstructions"])

    def test_markdown_followup_preserves_old_rich_history_and_cas(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture(temporary)
            store = ReaderStore(temporary)
            try:
                anno = store.create("paper_a", {"user_note": "第一问", "block_id": "body-1"})["annotation"]
                store.register_bridge("b1", "codex")
                first = store.claim("b1")["task"]
                store.acknowledge(first["id"], "b1", first["claim_token"], "thread-a", "old-turn")
                store.result(first["id"], "b1", first["claim_token"], {"answer": "old", "answer_content": rich("旧富答案"), "sources": [SOURCE]})
                old_history = deepcopy(store.get("paper_a", anno["id"])["history"][0])
                store.edit("paper_a", anno["id"], {"revision": 1, "user_note": "下一步呢？"}, "followup")
                second = store.claim("b1")["task"]
                store.acknowledge(second["id"], "b1", second["claim_token"], "thread-a", "new-turn")
                parsed = CodexAppServerBridge._answer([{"type": "agentMessage", "text": json.dumps({"answer": MARKDOWN, "sources": [SOURCE]})}])
                self.assertTrue(store.result(second["id"], "b1", second["claim_token"], parsed)["applied"])
                saved = store.get("paper_a", anno["id"])
                self.assertEqual(MARKDOWN, saved["answer"])
                self.assertNotIn("answer_content", saved)
                self.assertEqual(old_history, saved["history"][0])
                self.assertEqual(MARKDOWN, saved["history"][1]["answer"])
                self.assertFalse(store.result(first["id"], "b1", first["claim_token"], {"answer": "late", "sources": []})["applied"])
                self.assertEqual(old_history, store.get("paper_a", anno["id"])["history"][0])
            finally:
                store.close()

    def test_service_rejects_unsafe_or_unknown_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture(temporary)
            store = ReaderStore(temporary)
            try:
                for source in ({**SOURCE, "url": "javascript:alert(1)"}, {**SOURCE, "content_id": "invented"}, {**SOURCE, "page": 99}):
                    with self.subTest(source=source), self.assertRaises(StoreError):
                        store.validate_sources("paper_a", [source])
                store.validate_sources("paper_a", [SOURCE])
            finally:
                store.close()

    def test_32_citations_accept_real_nested_body_ids_but_not_component_fallback_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            paper=fixture(temporary)
            path=paper/"paper-data.js"
            data=json.loads(path.read_text(encoding="utf-8").split("=",1)[1].strip().rstrip(";"))
            data["content_contract_version"]="3.2"
            data["explanation"]["sections"][0]["blocks"].extend([
                {"id":"local-derivation","type":"details","blocks":[{"id":"inner-quote","type":"quote","blocks":[
                    {"id":"inner-formula","type":"formula","latex":"x+y=z","source_refs":[{"page":1}]}]}]},
                {"id":"aid","type":"learning_aid","aid":{"fallback":{"version":1,"blocks":[
                    {"id":"local-fallback","type":"paragraph","text":"isolated component fallback"}]}}}])
            path.write_text("window.PAPER_DATA = "+json.dumps(data)+";",encoding="utf-8")
            store=ReaderStore(temporary)
            try:
                store.validate_sources("paper_a",[{"content_id":"inner-formula","page":1}])
                with self.assertRaises(StoreError):
                    store.validate_sources("paper_a",[{"content_id":"local-fallback","page":1}])
                data["content_contract_version"]="3.1"
                path.write_text("window.PAPER_DATA = "+json.dumps(data)+";",encoding="utf-8")
                with self.assertRaises(StoreError):
                    store.validate_sources("paper_a",[{"content_id":"inner-formula","page":1}])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
