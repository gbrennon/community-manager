"""Tests for the notifications subsystem (ports-and-adapters)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from community_manager.notifications import ReviewNotifier, build_notifier
from community_manager.notifications.linux_notifier import LinuxNotifier
from community_manager.notifications.macos_notifier import MacOSNotifier
from community_manager.notifications.null_notifier import NullNotifier
from community_manager.notifications.windows_notifier import WindowsNotifier
from community_manager.notifications.protocol import ReviewNotifier as ReviewNotifierProtocol
from community_manager.sandbox.reviewer import ReviewResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    *,
    success: bool = True,
    crash: bool = False,
    url: str = "https://github.com/cline/cline/issues/1",
    title: str = "some issue",
) -> ReviewResult:
    return ReviewResult(
        issue_url=url,
        issue_title=title,
        sandbox_id="test-sandbox",
        success=success,
        crash_observed=crash,
        reproduced=success,
        verdict="CONFIRMED" if crash else "OK",
    )


REPORT = Path("/tmp/verdicts.md")


# ---------------------------------------------------------------------------
# Protocol / ABC
# ---------------------------------------------------------------------------

class TestReviewNotifierProtocol:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            ReviewNotifierProtocol()  # type: ignore[abstract]

    def test_build_summary_clean(self) -> None:
        results = [_make_result()]
        title, body = ReviewNotifierProtocol.build_summary(results, REPORT)
        assert "✅" in title
        assert "1 issue reviewed" in body
        assert str(REPORT) in body

    def test_build_summary_with_crash(self) -> None:
        results = [_make_result(crash=True), _make_result()]
        title, body = ReviewNotifierProtocol.build_summary(results, REPORT)
        assert "💥" in title
        assert "Crashes: 1" in body

    def test_build_summary_with_failure(self) -> None:
        results = [_make_result(success=False)]
        title, body = ReviewNotifierProtocol.build_summary(results, REPORT)
        assert "⚠" in title
        assert "Failures: 1" in body

    def test_build_summary_plural_issues(self) -> None:
        results = [_make_result(), _make_result()]
        title, body = ReviewNotifierProtocol.build_summary(results, REPORT)
        assert "2 issues reviewed" in body


# ---------------------------------------------------------------------------
# NullNotifier
# ---------------------------------------------------------------------------

class TestNullNotifier:
    def test_notify_never_raises(self) -> None:
        NullNotifier().notify([_make_result()], REPORT)

    def test_notify_empty_results(self) -> None:
        NullNotifier().notify([], REPORT)


# ---------------------------------------------------------------------------
# LinuxNotifier
# ---------------------------------------------------------------------------

class TestLinuxNotifier:
    def test_calls_notify_send_when_available(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            LinuxNotifier().notify([_make_result()], REPORT)
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0][0] == "notify-send"

    def test_urgency_critical_on_crash(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            LinuxNotifier().notify([_make_result(crash=True)], REPORT)
            assert "critical" in mock_run.call_args[0][0]

    def test_urgency_low_on_clean(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            LinuxNotifier().notify([_make_result()], REPORT)
            assert "low" in mock_run.call_args[0][0]

    def test_skips_when_notify_send_missing(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("shutil.which", return_value=None):
            LinuxNotifier().notify([_make_result()], REPORT)
        assert "notify-send not found" in capsys.readouterr().err

    def test_does_not_raise_on_subprocess_error(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run", side_effect=OSError("broken")),
        ):
            LinuxNotifier().notify([_make_result()], REPORT)


# ---------------------------------------------------------------------------
# MacOSNotifier
# ---------------------------------------------------------------------------

class TestMacOSNotifier:
    def test_calls_osascript(self) -> None:
        with patch("subprocess.run") as mock_run:
            MacOSNotifier().notify([_make_result()], REPORT)
            assert mock_run.call_args[0][0][0] == "osascript"

    def test_script_contains_app_name(self) -> None:
        with patch("subprocess.run") as mock_run:
            MacOSNotifier().notify([_make_result()], REPORT)
            script = mock_run.call_args[0][0][2]
            assert "community-manager" in script

    def test_does_not_raise_on_subprocess_error(self) -> None:
        with patch("subprocess.run", side_effect=OSError("broken")):
            MacOSNotifier().notify([_make_result()], REPORT)


# ---------------------------------------------------------------------------
# WindowsNotifier
# ---------------------------------------------------------------------------

class TestWindowsNotifier:
    def test_calls_powershell(self) -> None:
        with patch("subprocess.run") as mock_run:
            WindowsNotifier().notify([_make_result()], REPORT)
            assert mock_run.call_args[0][0][0] == "powershell"

    def test_does_not_raise_on_subprocess_error(self) -> None:
        with patch("subprocess.run", side_effect=OSError("broken")):
            WindowsNotifier().notify([_make_result()], REPORT)


# ---------------------------------------------------------------------------
# build_notifier() factory — OS detection
# ---------------------------------------------------------------------------

class TestBuildNotifier:
    def test_linux(self) -> None:
        with patch("platform.system", return_value="Linux"):
            assert isinstance(build_notifier(), LinuxNotifier)

    def test_macos(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            assert isinstance(build_notifier(), MacOSNotifier)

    def test_windows(self) -> None:
        with patch("platform.system", return_value="Windows"):
            assert isinstance(build_notifier(), WindowsNotifier)

    def test_unknown_os_falls_back_to_null(self) -> None:
        with patch("platform.system", return_value="FreeBSD"):
            assert isinstance(build_notifier(), NullNotifier)

    def test_result_is_review_notifier_subclass(self) -> None:
        with patch("platform.system", return_value="Linux"):
            assert isinstance(build_notifier(), ReviewNotifier)
