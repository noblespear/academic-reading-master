"""Deterministic local adapter; never impersonates a live model answer."""
import time
from .base import HostBridge, UnknownDelivery


class MockBridge(HostBridge):
    def __init__(self, config):
        self.config = config
        self.turns = {}

    def session(self, paper_dir, thread_id=None):
        return thread_id or "mock-thread-" + paper_dir.name

    def start(self, thread_id, task, context):
        turn_id = "mock-turn-" + task["id"]
        self.turns[turn_id] = {"task": task, "context": context}
        return turn_id

    def wait(self, thread_id, turn_id, pulse):
        deadline = time.monotonic() + float(self.config.get("mock_delay", 0.2))
        while time.monotonic() < deadline:
            if pulse():
                raise RuntimeError("Mock turn interrupted")
            time.sleep(0.05)
        turn = self.turns[turn_id]
        return {"answer": "[MOCK 测试回答] " + turn["task"]["payload"]["user_note"],
                "sources": [{"label": "Mock fixture", "locator": "test-only", "source_id": "mock", "content_id": turn["task"]["payload"].get("block_id", ""), "url": "", "page": None}]}

    def recover(self, task, pulse):
        raise UnknownDelivery("Mock sessions are in-memory; recovery requires an explicit retry")

    def interrupt(self, thread_id, turn_id):
        pass

    def close(self):
        pass
