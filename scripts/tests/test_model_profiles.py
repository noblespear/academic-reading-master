"""Agent-owned settings, disabled browser configuration, and QA route regressions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from model_profiles import (ROLES, check_profiles, get_role_profile, load_profiles, save_profiles,
                            resolve_role_config, route_fingerprint)
from reader_store import ReaderStore, StoreError
from serve_reader_v3 import ReaderServer
from tests.test_reader_v3 import fixture


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.vault.mkdir()
        self.user_home = Path(self.temp.name) / "user-home"
        self.environment = patch.dict("os.environ", {"CODEX_HOME": str(self.user_home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_absent_roles_preserve_legacy_and_transport(self):
        base = {"model": "legacy", "effort": "medium", "codex_bin": "private-path", "url": "loopback", "token": "transport-only"}
        for role in ROLES:
            self.assertEqual(base, resolve_role_config(self.vault, role, base))
        self.assertFalse((self.vault / ".reader").exists())
        self.assertFalse(self.user_home.exists())

    def test_user_level_discovery_and_vault_fields_override_without_copying_defaults(self):
        save_profiles(None, {"writing": {"model": "user-writer", "effort": "high"},
                             "qa": {"model": "user-qa", "effort": "low"}})
        self.assertEqual("user-writer", load_profiles()["writing"]["model"])

        path = self.user_home / "academic-reading-master" / "model-profiles.json"
        self.assertTrue(path.is_file())
        self.assertFalse((self.vault / ".reader").exists())
        save_profiles(self.vault, {"writing": {"effort": "max"}})
        self.assertEqual({"model": "user-writer", "effort": "max"}, load_profiles(self.vault)["writing"])
        self.assertEqual("user-qa", resolve_role_config(self.vault, "qa")["model"])
        vault_path = self.vault / ".reader" / "model-profiles.json"
        self.assertEqual({"writing": {"effort": "max"}}, json.loads(vault_path.read_text(encoding="utf-8"))["profiles"])
        save_profiles(self.vault, {"writing": {"model": ""}})
        self.assertEqual({"effort": "max", "codex_bin": "private"},
                         resolve_role_config(self.vault, "writing", {"model": "legacy", "codex_bin": "private"}))
        self.assertEqual("user-writer", load_profiles()["writing"]["model"])

    def test_default_user_path_without_codex_home_uses_home_codex_directory(self):
        home = Path(self.temp.name) / "home"
        with patch("model_profiles.os.environ", {}), patch("model_profiles.Path.home", return_value=home):
            save_profiles(None, {"qa": {"model": "", "effort": ""}})
            self.assertTrue((home / ".codex" / "academic-reading-master" / "model-profiles.json").is_file())
            self.assertTrue(get_role_profile(None, "qa")["configured"])
            self.assertEqual({}, resolve_role_config(None, "qa", {"model": "legacy", "effort": "high"}))

    def test_explicit_inheritance_is_configured_and_only_required_roles_are_missing(self):
        result = check_profiles(self.vault, ("writing", "drawing"))
        self.assertEqual(["writing", "drawing"], result["missing_roles"])
        self.assertFalse(result["configured"])
        self.assertFalse(result["profiles"]["writing"]["configured"])
        save_profiles(None, {"writing": {"model": "", "effort": ""}, "drawing": {}})
        self.assertTrue(get_role_profile(self.vault, "writing")["configured"])
        self.assertEqual(["drawing"], check_profiles(self.vault, ("writing", "drawing"))["missing_roles"])
        save_profiles(self.vault, {"drawing": {"model": "chosen-drawing"}})
        result = check_profiles(self.vault, ("writing", "drawing"))
        self.assertTrue(result["configured"])
        self.assertEqual([], result["missing_roles"])
        self.assertNotIn("translation", result["profiles"])
        self.assertEqual(["qa", "translation"], check_profiles(self.vault)["missing_roles"])

    def test_read_check_and_save_never_launch_or_connect_models(self):
        with patch("subprocess.Popen") as launch, patch("socket.create_connection") as connect:
            save_profiles(None, {"writing": {"model": "writer", "effort": "max"}})
            save_profiles(self.vault, {"qa": {"model": "", "effort": ""}})
            load_profiles(self.vault)
            check_profiles(self.vault, ("writing", "qa"))
            resolve_role_config(self.vault, "qa")
            launch.assert_not_called()
            connect.assert_not_called()

    def test_four_roles_isolated_and_explicit_empty_inherits_host(self):
        for index, role in enumerate(ROLES):
            save_profiles(self.vault, {role: {"model": "user-model-" + str(index), "effort": "low"}})
        for index, role in enumerate(ROLES):
            self.assertEqual("user-model-" + str(index), resolve_role_config(self.vault, role)["model"])
        base = {"model": "legacy", "effort": "medium", "codex_bin": "private-path", "token": "never-save-this"}
        save_profiles(self.vault, {"qa": {"model": "", "effort": None}})
        resolved = resolve_role_config(self.vault, "qa", base)
        self.assertEqual({"codex_bin": "private-path", "token": "never-save-this"}, resolved)
        self.assertEqual("legacy", base["model"])
        self.assertEqual("user-model-0", resolve_role_config(self.vault, "writing")["model"])
        raw = (self.vault / ".reader" / "model-profiles.json").read_text(encoding="utf-8")
        self.assertNotIn("never-save-this", raw)
        self.assertNotIn("codex_bin", raw)
        self.assertTrue(get_role_profile(self.vault, "qa")["configured"])

    def test_partial_field_update_and_concurrent_role_saves(self):
        save_profiles(self.vault, {"qa": {"model": "chosen"}})
        self.assertEqual({"model": "chosen", "effort": "high"}, resolve_role_config(self.vault, "qa", {"effort": "high"}))
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda role: save_profiles(self.vault, {role: {"model": role + "-model", "effort": "xhigh"}}), ROLES))
        self.assertEqual(set(ROLES), set(load_profiles(self.vault)))
        self.assertEqual([], list((self.vault / ".reader").glob("*.tmp")))

    def test_strict_role_key_type_length_and_effort_validation(self):
        invalid = [{"typo": {}}, {"qa": {"token": "secret"}}, {"qa": {"model": "x" * 201}},
                   {"qa": {"model": "two words"}}, {"qa": {"model": "newline\n"}},
                   {"qa": {"model": 3}}, {"qa": {"effort": "automatic"}}, {"qa": []}, {}, []]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_profiles(self.vault, value)
        state = self.vault / ".reader"
        state.mkdir(exist_ok=True)
        path = state / "model-profiles.json"
        for value in ({"version": 1, "profiles": {"qa": {"api_key": "bad"}}},
                      {"version": True, "profiles": {}}, {"version": 1, "profiles": {}, "extra": "bad"}):
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_profiles(self.vault)
        path.write_bytes(b" " * 8193)
        with self.assertRaises(ValueError):
            load_profiles(self.vault)

    def test_cli_bounded_json_and_inheritance_without_model_process(self):
        command = [sys.executable, str(SCRIPTS / "model_profiles.py"), "--vault", str(self.vault)]
        saved = subprocess.run(command + ["set", "drawing", "--model", "custom/model-v2", "--effort", "high"], capture_output=True, text=True, check=True)
        self.assertEqual("custom/model-v2", json.loads(saved.stdout)["model"])
        all_roles = subprocess.run(command + ["get"], capture_output=True, text=True, check=True)
        self.assertLess(len(all_roles.stdout), 8192)
        self.assertEqual(set(ROLES), set(json.loads(all_roles.stdout)["profiles"]))
        inherited = subprocess.run(command + ["set", "drawing", "--inherit"], capture_output=True, text=True, check=True)
        self.assertEqual("", json.loads(inherited.stdout)["model"])
        self.assertEqual({}, resolve_role_config(self.vault, "drawing", {"model": "old", "effort": "low"}))

    def test_cli_defaults_to_user_settings_and_reports_only_requested_missing_roles(self):
        command = [sys.executable, str(SCRIPTS / "model_profiles.py")]
        result = subprocess.run(command + ["check", "writing", "drawing"], capture_output=True, text=True, check=True)
        self.assertEqual(["writing", "drawing"], json.loads(result.stdout)["missing_roles"])
        self.assertFalse(self.user_home.exists())
        subprocess.run(command + ["set", "writing", "--inherit"], capture_output=True, text=True, check=True)
        subprocess.run(command + ["set", "drawing", "--model", "user-drawing", "--effort", "high"], capture_output=True, text=True, check=True)
        result = subprocess.run(command + ["check", "writing", "drawing"], capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)["configured"])
        result = subprocess.run(command + ["--vault", str(self.vault), "get"], capture_output=True, text=True, check=True)
        profiles = json.loads(result.stdout)
        self.assertEqual("user-drawing", profiles["profiles"]["drawing"]["model"])
        self.assertEqual(["qa", "translation"], profiles["missing_roles"])
        self.assertFalse((self.vault / ".reader").exists())


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.environment = patch.dict("os.environ", {"CODEX_HOME": str(self.vault / "user-home")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.paper = fixture(self.vault)
        self.store = ReaderStore(self.vault)
        self.store.register_bridge("b1", "codex")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def claim(self):
        self.store.create("paper_a", {"user_note": "New question", "block_id": "body-1"})
        return self.store.claim("b1")["task"]

    def prepare(self, task, config):
        return self.store.prepare_session_route(task["id"], "b1", task["claim_token"], route_fingerprint(config))

    def ack(self, task, thread):
        return self.store.acknowledge(task["id"], "b1", task["claim_token"], thread)

    def complete(self, task):
        self.store.result(task["id"], "b1", task["claim_token"], {"answer": "Completed", "sources": []})

    def test_legacy_default_preserves_thread_and_confirms_route_only_after_ack(self):
        first = self.claim()
        self.ack(first, "legacy-thread")
        self.complete(first)
        second = self.claim()
        prepared = self.prepare(second, {})
        self.assertEqual({"thread_id": "legacy-thread", "session_generation": 0}, prepared)
        self.assertEqual("", self.store.db.execute("SELECT model_route FROM sessions").fetchone()[0])
        self.ack(second, prepared["thread_id"])
        self.assertEqual(route_fingerprint({}), self.store.db.execute("SELECT model_route FROM sessions").fetchone()[0])

    def test_same_route_restart_reuses_and_changed_model_or_effort_gets_new_thread(self):
        config = {"model": "chosen-a", "effort": "high"}
        first = self.claim()
        self.assertIsNone(self.prepare(first, config)["thread_id"])
        self.ack(first, "thread-a")
        self.complete(first)
        self.store.close()
        self.store = ReaderStore(self.vault)
        self.store.register_bridge("b1", "codex")
        second = self.claim()
        self.assertEqual("thread-a", self.prepare(second, config)["thread_id"])
        self.ack(second, "thread-a")
        self.complete(second)
        for index, changed in enumerate(({"model": "chosen-b", "effort": "high"}, {"model": "chosen-b", "effort": "low"}, {}), 1):
            task = self.claim()
            prepared = self.prepare(task, changed)
            self.assertEqual({"thread_id": None, "session_generation": index}, prepared)
            self.assertEqual(prepared, self.prepare(task, changed))
            self.ack(task, "thread-" + str(index))
            self.complete(task)

    def test_legacy_explicit_route_is_not_assumed_to_match(self):
        first = self.claim()
        self.ack(first, "old-untracked")
        self.complete(first)
        next_task = self.claim()
        self.assertIsNone(self.prepare(next_task, {"model": "possibly-different"})["thread_id"])
        self.assertEqual(1, self.store._task(next_task["id"])["session_generation"])

    def test_running_unknown_and_expired_claim_cannot_switch_route(self):
        task = self.claim()
        self.prepare(task, {})
        self.ack(task, "live-thread")
        with self.assertRaises(StoreError):
            self.prepare(task, {"model": "changed"})
        self.store.mark_unknown(task["id"], "b1", task["claim_token"], "unknown delivery")
        with self.assertRaises(StoreError):
            self.prepare(task, {"model": "changed"})
        self.store.create("paper_a", {"user_note": "Blocked successor"})
        self.assertIsNone(self.store.claim("b1")["task"])
        self.assertEqual("live-thread", self.store.db.execute("SELECT thread_id FROM sessions").fetchone()[0])

    def test_bridge_changed_profile_creates_thread_without_old_chat_and_reuses_unchanged(self):
        from bridge_client import run
        from bridges.codex_app_server import CodexAppServerBridge
        store, calls = self.store, []
        class API:
            def __init__(self, config):
                pass
            def post(self, action, data=None, timeout=35):
                data = data or {}
                if action == "register":
                    return store.register_bridge("b1", "codex", data.get("info"))
                if action == "heartbeat":
                    return store.heartbeat("b1", data.get("tasks", []), data.get("info"))
                if action == "recover":
                    return {"tasks": []}
                if action == "claim":
                    result = store.claim("b1")
                    return result if result["task"] else {"stop": True}
                if action == "session-route":
                    return store.prepare_session_route(data["task_id"], "b1", data["claim_token"], data["route"])
                if action == "ack":
                    return store.acknowledge(data["task_id"], "b1", data["claim_token"], data.get("thread_id"), data.get("turn_id"))
                if action == "result":
                    return store.result(data["task_id"], "b1", data["claim_token"], data.get("result"), data.get("error"))
                if action == "unregister":
                    return {}
                raise AssertionError(action)
        class RPC:
            closed = False
            def rpc(self, method, params, timeout=60):
                calls.append((method, params))
                if method == "model/list":
                    return {"data": []}
                if method in ("thread/start", "thread/resume"):
                    return {"thread": {"id": params.get("threadId") or "thread-" + str(len(calls)), "turns": []}}
                if method == "turn/start":
                    return {"turn": {"id": "turn-" + str(len(calls))}}
                if method == "thread/read":
                    return {"thread": {"id": params["threadId"], "turns": []}}
                return {}
            def close(self):
                pass
        def complete(adapter, thread, turn, pulse):
            adapter.active.discard((thread, turn))
            return {"answer": "Done", "sources": []}
        def enqueue(note="Question"):
            return store.create("paper_a", {"user_note": note, "block_id": "body-1"})
        with patch("bridge_client.ReaderAPI", API), patch("bridge_client.build_context", return_value={}), \
                patch("bridges.codex_app_server.AppServer", side_effect=lambda *_: RPC()), \
                patch.object(CodexAppServerBridge, "wait", complete):
            enqueue();enqueue()
            run({"vault": str(self.vault)})
            self.assertEqual(1, sum(method == "thread/start" for method, _ in calls))
            self.assertEqual(0, sum(method == "thread/resume" for method, _ in calls))
            enqueue();run({"vault": str(self.vault)})
            self.assertEqual(1, sum(method == "thread/start" for method, _ in calls))
            self.assertEqual(1, sum(method == "thread/resume" for method, _ in calls))
            annotation = enqueue()["annotation"]
            # Deliberately supply old QA text in storage: the host wire must drop it.
            task_id = annotation["task_id"]
            task = store._task(task_id)
            task["payload"]["history"] = [{"user_note": "OLD-QUESTION-SECRET", "answer": "OLD-ANSWER-SECRET"}]
            store.db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(task["payload"]), task_id));store.db.commit()
            save_profiles(self.vault, {"qa": {"model": "new-qa", "effort": "low"}})
            run({"vault": str(self.vault)})
        self.assertEqual(2, sum(method == "thread/start" for method, _ in calls))
        self.assertEqual(1, sum(method == "thread/resume" for method, _ in calls))
        turns = [params for method, params in calls if method == "turn/start"]
        self.assertNotIn("model", turns[0]);self.assertNotIn("effort", turns[0])
        self.assertEqual("new-qa", turns[-1]["model"])
        self.assertEqual("low", turns[-1]["effort"])
        self.assertNotIn("OLD-QUESTION-SECRET", json.dumps(turns[-1]))
        self.assertNotIn("OLD-ANSWER-SECRET", json.dumps(turns[-1]))

    def test_recovery_preserves_old_thread_model_and_restores_next_task_profile(self):
        from bridge_client import recover_existing_session
        from bridges.codex_app_server import CodexAppServerBridge
        from bridges.base import UnknownDelivery
        calls = []
        class RPC:
            closed = False
            def rpc(self, method, params, timeout=60):
                calls.append((method, params))
                if method == "model/list":
                    return {"data": []}
                if method == "thread/resume":
                    return {"thread": {"id": params["threadId"], "turns": []}}
                if method == "thread/read":
                    return {"thread": {"id": params["threadId"], "turns": [{"id": "old-turn", "status": "completed", "items": [{"type": "agentMessage", "text": '{"answer":"Original model result","sources":[]}'}]}]}}
                if method == "thread/start":
                    return {"thread": {"id": "next-thread", "turns": []}}
                if method == "turn/start":
                    return {"turn": {"id": "next-turn"}}
                raise AssertionError(method)
        snapshot = {"vault": str(self.vault), "model": "next-model", "effort": "high", "codex_bin": "host-path"}
        with patch("bridges.codex_app_server.AppServer", return_value=RPC()):
            adapter = CodexAppServerBridge(snapshot)
        result = recover_existing_session(adapter, {"id": "old-task", "paper_id": "paper_a", "thread_id": "old-thread", "turn_id": "old-turn"}, lambda: False)
        self.assertEqual("Original model result", result["answer"])
        resumes = [params for method, params in calls if method == "thread/resume"]
        self.assertEqual("old-thread", resumes[0]["threadId"])
        self.assertNotIn("model", resumes[0]);self.assertNotIn("effort", resumes[0])
        self.assertFalse(any(method == "turn/start" for method, _ in calls))
        self.assertIs(snapshot, adapter.config)
        thread = adapter.session(self.paper)
        adapter.start(thread, {"id": "next-task", "paper_id": "paper_a", "payload": {"user_note": "Next question"}}, {})
        self.assertEqual("next-model", calls[-1][1]["model"])
        self.assertEqual("high", calls[-1][1]["effort"])
        with patch.object(adapter, "recover", side_effect=UnknownDelivery("still unknown")), self.assertRaises(UnknownDelivery):
            recover_existing_session(adapter, {}, lambda: False)
        self.assertIs(snapshot, adapter.config)


class SettingsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.environment = patch.dict("os.environ", {"CODEX_HOME": str(self.vault / "user-home")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        fixture(self.vault)
        self.server = ReaderServer(self.vault, "paper_a", bridge="none", config={"codex_bin": "host-executable"})
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.bridge_process = None
        self.server.shutdown();self.worker.join(2);self.server.cleanup();self.temp.cleanup()

    def request(self, body=None, token=True, origin=None, method=None):
        headers = {"X-Reader-Token": self.server.token} if token else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        request = Request(self.server.base_url + "/api/model-settings", data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_settings_api_does_not_exist_or_allow_any_configuration_mutation(self):
        with patch("serve_reader_v3.subprocess.Popen") as launch, patch.object(self.server.translations, "generate") as generate:
            for method in ("GET", "POST", "PATCH", "DELETE"):
                with self.subTest(method=method), self.assertRaises(HTTPError) as result:
                    self.request(None if method == "GET" else {"profiles": {"qa": {"model": "forbidden"}}}, method=method)
                self.assertEqual(404, result.exception.code)
            launch.assert_not_called();generate.assert_not_called()
        self.assertEqual({}, load_profiles(self.vault))
        self.assertFalse((self.vault / ".reader" / "model-profiles.json").exists())
        self.assertEqual(0, self.server.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_removed_api_keeps_general_token_and_origin_guards(self):
        for options, status in (({"token": False}, 401), ({"origin": "https://outside.invalid"}, 403)):
            with self.subTest(options=options), self.assertRaises(HTTPError) as result:
                self.request(**options)
            self.assertEqual(status, result.exception.code)

    def test_next_connection_consumes_snapshot_and_keeps_transport(self):
        save_profiles(None, {"qa": {"model": "qa-picked", "effort": "medium"}})
        save_profiles(self.vault, {"qa": {"effort": "xhigh"}})
        self.server.bridge_kind = "codex"
        child = Mock(poll=Mock(return_value=None))
        with patch("serve_reader_v3.subprocess.Popen", return_value=child) as launch:
            self.assertTrue(self.server._start_bridge_locked())
        config = json.loads(self.server.bridge_config_file.read_text(encoding="utf-8"))
        self.assertEqual("qa-picked", config["model"])
        self.assertEqual("xhigh", config["effort"])
        self.assertEqual("host-executable", config["codex_bin"])
        self.assertTrue(config["qa_profile_snapshot"])
        self.assertEqual(1, launch.call_count)


if __name__ == "__main__":
    unittest.main()
