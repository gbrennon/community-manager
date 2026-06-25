"""Port (interface) for review completion notifications.

Implementations live alongside this file — one per OS adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from community_manager.sandbox.reviewer import ReviewResult


class ReviewNotifier(ABC):
    """Send a desktop notification when a review batch finishes.

    Implementations must be side-effect-only: they must never raise — if
    the underlying OS mechanism fails, log silently and return.
    """

    @abstractmethod
    def notify(self, results: list[ReviewResult], report_path: Path) -> None:
        """Fire a notification summarising *results*.

        Args:
            results:     The completed review results.
            report_path: Path to the verdicts file that was written.
        """

    # ------------------------------------------------------------------
    # Shared helpers available to every adapter
    # ------------------------------------------------------------------

    @staticmethod
    def build_summary(results: list[ReviewResult], report_path: Path) -> tuple[str, str]:
        """Return (title, body) suitable for any desktop notification API."""
        total = len(results)
        crashes = sum(1 for r in results if r.crash_observed)
        failures = sum(1 for r in results if not r.success)

        if crashes:
            title = f"community-manager: {crashes} crash{'es' if crashes > 1 else ''} found 💥"
        elif failures:
            title = f"community-manager: review done ⚠ ({failures} failure{'s' if failures > 1 else ''})"
        else:
            title = "community-manager: review done ✅"

        lines: list[str] = [f"{total} issue{'s' if total != 1 else ''} reviewed"]
        if crashes:
            lines.append(f"Crashes: {crashes}")
        if failures:
            lines.append(f"Failures: {failures}")
        lines.append(f"Report: {report_path}")
        body = " · ".join(lines)
        return title, body
