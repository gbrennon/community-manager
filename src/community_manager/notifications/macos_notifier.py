"""macOS adapter — uses ``osascript`` (built into every macOS install)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from community_manager.notifications.protocol import ReviewNotifier
from community_manager.sandbox.reviewer import ReviewResult


class MacOSNotifier(ReviewNotifier):
    """Send a desktop notification via ``osascript`` / AppleScript.

    ``osascript`` ships with every macOS installation — no external
    dependency required.
    """

    def notify(self, results: list[ReviewResult], report_path: Path) -> None:
        title, body = self.build_summary(results, report_path)
        # AppleScript requires double-quotes to be escaped inside the string.
        safe_title = title.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')
        script = (
            f'display notification "{safe_body}" '
            f'with title "{safe_title}" '
            f'sound name "Glass"'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] osascript failed: {exc}", file=sys.stderr)
