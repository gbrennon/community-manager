"""Windows adapter — uses a PowerShell toast notification."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from community_manager.notifications.protocol import ReviewNotifier
from community_manager.sandbox.reviewer import ReviewResult


class WindowsNotifier(ReviewNotifier):
    """Send a Windows toast notification via PowerShell.

    Uses the ``Windows.UI.Notifications`` WinRT API, available on
    Windows 8+ without any external dependencies.
    """

    def notify(self, results: list[ReviewResult], report_path: Path) -> None:
        title, body = self.build_summary(results, report_path)
        # Escape single quotes for PowerShell here-string safety.
        safe_title = title.replace("'", "\\'")
        safe_body = body.replace("'", "\\'")
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$template.SelectSingleNode('//text[@id=1]').InnerText = '{safe_title}'
$template.SelectSingleNode('//text[@id=2]').InnerText = '{safe_body}'
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('community-manager').Show($toast)
"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                check=False,
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] PowerShell toast failed: {exc}", file=sys.stderr)
