"""Text insertion adapters: paste, type, clipboard-only, and a fake for tests."""

from local_flow.insertion.base import FakeTextSink, InsertionManager, TextSink

__all__ = ["FakeTextSink", "InsertionManager", "TextSink"]
