"""Null adapter — silently discards every notification.

Used as a safe fallback on unsupported platforms or in tests.
"""

from __future__ import annotations

from pathlib import Path

from community_manager.notifications.protocol import ReviewNotifier
from community_manager.sandbox.reviewer import ReviewResult


class NullNotifier(ReviewNotifier):
    """No-op notifier — does nothing, never raises."""

    def notify(self, results: list[ReviewResult], report_path: Path) -> None:
        pass  # intentionally silent
