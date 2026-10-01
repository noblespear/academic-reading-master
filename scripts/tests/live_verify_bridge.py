#!/usr/bin/env python3
"""Opt-in actual Codex app-server verification; never run by unittest discovery.

Creates only a dedicated fixture vault under --output, launches detached reader,
submits two real model turns in one paper session, verifies SQLite persistence,
then explicitly stops the supervisor. Host authentication/configuration is used
locally. No API credentials are read, copied or printed.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from test_reader_v3 import fixture


def main():
    parser = argparse.ArgumentParser(description="OPT-IN live installed-Codex bridge test")
    parser.add_argument("--live", action="store_true", required=True, help="Acknowledges two real host model turns")
    parser.add_argument("--output", required=True, help="Dedicated test output directory, never a production vault")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--recover-restart", action="store_true", help="Also simulate a lost result and verify actual host history recovery after restart")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be empty or nonexistent")
    output.mkdir(parents=True, exist_ok=True)
    vault = output / "FixtureVault"
    fixture(vault)
    scripts = Path(__file__).resolve().parents[1]
    report = {"live": True, "ok": False, "steps": [], "started_at": time.time()}
    state = None
    try:
        launched = subprocess.run([sys.executable, str(scripts / "open_reader.py"), "--vault", str(vault),
                                   "paper_a", "--no-open", "--bridge", "codex"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=25)
        report["launcher_exit"] = launched.returncode
        report["launcher_output"] = launched.stdout
        if launched.returncode:
            raise RuntimeError("Detached service launcher failed: " + launched.stderr)
        state = json.loads((vault / ".reader/server.json").read_text(encoding="utf-8"))

        def api(path, body=None, method=None):
            request = Request(state["url"] + path, data=json.dumps(body).encode() if body is not None else None,
                method=method or ("POST" if body is not None else "GET"),
                headers={"Content-Type": "application/json", "X-Reader-Token": state["token"]})
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        report["steps"].append({"name": "service persists after launcher exit", "ping": api("/api/ping")})
        first = api("/api/papers/paper_a/annotations", {"block_id": "body-1", "user_note": "依据给定原文，edge collapse 的逆操作是什么？用一句中文回答，并引用 body-1 和 PDF 第1页。", "client_request_id": "live-first"})["annotation"]

        def wait_answer(aid, revision):
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                annotations = api("/api/papers/paper_a/annotations")["annotations"]
                current = next(a for a in annotations if a["id"] == aid)
                if current["revision"] == revision and current["status"] in ("answered", "failed"):
                    if current["status"] != "answered":
                        raise RuntimeError("Real host turn failed: " + current.get("error", "unknown"))
                    return current
                time.sleep(0.5)
            raise TimeoutError("Real model answer not received before test deadline")

        answered = wait_answer(first["id"], 1)
        with sqlite3.connect(vault / ".reader/state.sqlite3") as db:
            one = db.execute("SELECT thread_id,turn_id,state FROM tasks WHERE annotation_id=? AND revision=1", (first["id"],)).fetchone()
        report["steps"].append({"name": "first real turn", "answer": answered["answer"], "sources": answered["sources"],
                                 "thread_id": one[0], "turn_id": one[1], "state": one[2]})
        if args.recover_restart:
            first_host_pids = [b["info"].get("host_pid") for b in api("/api/status")["bridges"] if b["connected"]]
            api("/api/stop", {})
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                ended = json.loads((vault / ".reader/server.json").read_text(encoding="utf-8"))
                if ended.get("stopped"):
                    break
                time.sleep(0.2)
            else:
                raise TimeoutError("First service did not stop before recovery simulation")
            # Test-only fault injection: host really completed this turn, but
            # simulate losing the HTTP result before SQLite publication.
            with sqlite3.connect(vault / ".reader/state.sqlite3") as db:
                stored = json.loads(db.execute("SELECT body FROM annotations WHERE paper_id='paper_a' AND id=?", (first["id"],)).fetchone()[0])
                stored.update(answer="", sources=[], status="running", unread=False)
                stored["history"][0].update(answer="", sources=[], status="running")
                db.execute("UPDATE annotations SET body=? WHERE paper_id='paper_a' AND id=?", (json.dumps(stored, ensure_ascii=False), first["id"]))
                db.execute("UPDATE tasks SET state='unknown',result=NULL,error='simulated lost HTTP result',lease_until=0 WHERE annotation_id=? AND revision=1", (first["id"],))
            restarted = subprocess.run([sys.executable, str(scripts / "open_reader.py"), "--vault", str(vault),
                "paper_a", "--no-open", "--bridge", "codex"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=25)
            if restarted.returncode:
                raise RuntimeError("Recovery restart launcher failed")
            state = json.loads((vault / ".reader/server.json").read_text(encoding="utf-8"))
            restored = wait_answer(first["id"], 1)
            with sqlite3.connect(vault / ".reader/state.sqlite3") as db:
                restored_task = db.execute("SELECT thread_id,turn_id,state FROM tasks WHERE annotation_id=? AND revision=1", (first["id"],)).fetchone()
                count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            if count != 1 or restored_task != one or restored["answer"] != answered["answer"]:
                raise AssertionError("Recovery changed task identity/turn or re-generated the answer")
            new_host_pids = [b["info"].get("host_pid") for b in api("/api/status")["bridges"] if b["connected"]]
            if set(first_host_pids) & set(new_host_pids):
                raise AssertionError("Recovery must use a new independent app-server process")
            report["steps"].append({"name": "actual thread history recovers lost result after service restart",
                "fault_injection": "fixture task marked unknown, published answer removed", "task_count": count,
                "thread_id": restored_task[0], "turn_id": restored_task[1], "answer_unchanged": True,
                "old_host_pids": first_host_pids, "new_host_pids": new_host_pids, "resubmitted": False})
        follow = api("/api/papers/paper_a/annotations/" + first["id"] + "/followups",
            {"revision": 1, "user_note": "接着上问：请只复述刚才答案里的操作英文名，并仍引用同一段来源。", "client_request_id": "live-followup"})["annotation"]
        second = wait_answer(first["id"], 2)
        with sqlite3.connect(vault / ".reader/state.sqlite3") as db:
            two = db.execute("SELECT thread_id,turn_id,state FROM tasks WHERE annotation_id=? AND revision=2", (first["id"],)).fetchone()
        if one[0] != two[0] or one[1] == two[1]:
            raise AssertionError("Followup must reuse paper thread and create distinct turn")
        if len(second["history"]) != 2 or second["history"][0]["answer"] != answered["answer"]:
            raise AssertionError("Answer history was not preserved")
        projection = json.loads((vault / "papers/paper_a/annotations.json").read_text(encoding="utf-8"))
        if projection["annotations"][0]["answer"] != second["answer"]:
            raise AssertionError("JSON projection differs from durable model answer")
        report["steps"].append({"name": "followup uses persistent per-paper session", "answer": second["answer"],
            "thread_id": two[0], "turn_id": two[1], "history_count": len(second["history"]), "projection_verified": True})
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        stopped = subprocess.run([sys.executable, str(scripts / "open_reader.py"), "--vault", str(vault), "--stop"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        report["stop_exit"] = stopped.returncode
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                final_state = json.loads((vault / ".reader/server.json").read_text(encoding="utf-8"))
                if final_state.get("stopped"):
                    report["stopped"] = True
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        report["completed_at"] = time.time()
        (output / "live-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # The report contains no local bearer tokens or host credentials.
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
