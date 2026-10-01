"""Minimal host interface for a persistent per-paper session.

Other hosts implement this interface and register their adapter name over HTTP.
Credentials and host protocol details must stay inside their local adapter.
"""
from abc import ABC, abstractmethod


class UnknownDelivery(RuntimeError):
    """The host may have accepted this turn; do not submit it again."""


class HostBridge(ABC):
    @abstractmethod
    def session(self, paper_dir, thread_id=None):
        """Start or resume and return a durable host thread id."""

    @abstractmethod
    def start(self, thread_id, task, context):
        """Start one turn and return its durable host turn id."""

    @abstractmethod
    def wait(self, thread_id, turn_id, pulse):
        """Wait for terminal turn, calling pulse regularly; return answer+sources."""

    @abstractmethod
    def recover(self, task, pulse):
        """Inspect the delivered turn; never re-send unverified delivery."""

    @abstractmethod
    def interrupt(self, thread_id, turn_id):
        """Interrupt the current host turn explicitly."""

    @abstractmethod
    def close(self):
        """Interrupt active turns and close the host subprocess."""
