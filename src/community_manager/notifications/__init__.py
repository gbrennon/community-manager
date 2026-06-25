"""Notification subsystem — ports-and-adapters for desktop alerts.

Public API
----------
    build_notifier() -> ReviewNotifier
        Detect the current OS and return the appropriate adapter.

    ReviewNotifier (ABC)
        The port — import this for type annotations.
"""

from __future__ import annotations

import platform

from community_manager.notifications.protocol import ReviewNotifier


def build_notifier() -> ReviewNotifier:
    """Return the best available notifier for the current OS.

    Detection order:
      - Linux   → LinuxNotifier  (notify-send / libnotify)
      - Darwin  → MacOSNotifier  (osascript / AppleScript)
      - Windows → WindowsNotifier (PowerShell toast)
      - other   → NullNotifier   (silent fallback)
    """
    system = platform.system()

    if system == "Linux":
        from community_manager.notifications.linux_notifier import LinuxNotifier
        return LinuxNotifier()

    if system == "Darwin":
        from community_manager.notifications.macos_notifier import MacOSNotifier
        return MacOSNotifier()

    if system == "Windows":
        from community_manager.notifications.windows_notifier import WindowsNotifier
        return WindowsNotifier()

    from community_manager.notifications.null_notifier import NullNotifier
    return NullNotifier()


__all__ = ["ReviewNotifier", "build_notifier"]
