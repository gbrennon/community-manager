"""Linux adapter — uses ``notify-send`` (libnotify)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from community_manager.notifications.protocol import ReviewNotifier
from community_manager.sandbox.reviewer import ReviewResult

# Urgency mapping: crashes are critical, failures are normal, clean is low.
_URGENCY_CRITICAL = "critical"
_URGENCY_NORMAL = "normal"
_URGENCY_LOW = "low"


class LinuxNotifier(ReviewNotifier):
    """Send a desktop notification via ``notify-send``.

    Requires ``libnotify-bin`` (Ubuntu/Debian) or ``libnotify`` (Arch/Fedora).
    If the binary is absent the call is silently skipped.
    """

    def notify(self, results: list[ReviewResult], report_path: Path) -> None:
        import shutil

        if not shutil.which("notify-send"):
            print(
                "[notify] notify-send not found — skipping desktop notification.",
                file=sys.stderr,
            )
            return

        title, body = self.build_summary(results, report_path)
        crashes = sum(1 for r in results if r.crash_observed)
        failures = sum(1 for r in results if not r.success)

        if crashes:
            urgency = _URGENCY_CRITICAL
        elif failures:
            urgency = _URGENCY_NORMAL
        else:
            urgency = _URGENCY_LOW

        try:
            subprocess.run(
                [
                    "notify-send",
                    "--urgency", urgency,
                    "--app-name", "community-manager",
                    "--icon", "dialog-information",
                    title,
                    body,
                ],
                check=False,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] notify-send failed: {exc}", file=sys.stderr)
