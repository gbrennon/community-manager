"""Tests for the autonomous sandbox subsystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from community_manager.issue_state import IssueState
from community_manager.sandbox.protocol import (
    SandboxConfig, SandboxProvider, SandboxResult,
)
from community_manager.sandbox.reviewer import (
    IssueReviewer, ReviewResult, process_exited_with_crash,
    convert_step_to_cline_command, parse_steps_from_issue_body,
)
from tests.conftest import FakeGitHubIssueFetcher


class CrashSimProvider(SandboxProvider):
    """Fake provider: returns SIGSEGV (139) when the ctrl+c step runs."""

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()
        self._boxes: dict[str, list[str]] = {}
        self._commands: dict[str, list[list[str]]] = {}

    async def launch(self) -> str:
        sid = "crash-sim-1"
        self._boxes[sid] = []
        self._commands[sid] = []
        return sid

    async def copy_in(self, sid: str, host: Path, sandbox: Path) -> None:
        self._boxes.setdefault(sid, []).append(str(host))

    async def exec(self, sid: str, cmd: list[str]) -> SandboxResult:
        self._commands.setdefault(sid, []).append(cmd)
        joined = " ".join(cmd)
        if "kill" in joined:
            return SandboxResult(exit_code=139, stdout="", stderr="Segmentation fault (core dumped)")
        if "coredumpctl" in joined:
            return SandboxResult(exit_code=0, stdout="TIME  PID  ... core.cline.1234", stderr="")
        return SandboxResult(exit_code=0, stdout="ok", stderr="")

    async def destroy(self, sid: str) -> None:
        self._boxes.pop(sid, None)

    async def is_healthy(self, sid: str) -> bool:
        return sid in self._boxes

    async def disconnect_network(self, sid: str) -> None:
        pass


class HappyProvider(SandboxProvider):
    """Fake provider: always succeeds."""

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()
        self._boxes: dict[str, list[str]] = {}
        self._commands: dict[str, list[list[str]]] = {}

    async def launch(self) -> str:
        sid = "fake-1"
        self._boxes[sid] = []
        self._commands[sid] = []
        return sid

    async def copy_in(self, sid: str, host: Path, sandbox: Path) -> None:
        pass

    async def exec(self, sid: str, cmd: list[str]) -> SandboxResult:
        self._commands.setdefault(sid, []).append(cmd)
        return SandboxResult(exit_code=0, stdout="ok", stderr="")

    async def destroy(self, sid: str) -> None:
        self._boxes.pop(sid, None)

    async def is_healthy(self, sid: str) -> bool:
        return sid in self._boxes

    async def disconnect_network(self, sid: str) -> None:
        pass


class TestIsCrash:
    def test_ok(self) -> None:
        assert not process_exited_with_crash(SandboxResult(exit_code=0, stdout="", stderr=""))

    def test_sigsegv(self) -> None:
        assert process_exited_with_crash(SandboxResult(exit_code=139, stdout="", stderr=""))

    def test_core_dumped_text(self) -> None:
        assert process_exited_with_crash(SandboxResult(exit_code=1, stdout="", stderr="core dumped"))


class TestStepToCmd:
    def test_open_cline(self) -> None:
        cmd = convert_step_to_cline_command("1. open cline")
        joined = " ".join(cmd)
        assert "cline" in joined

    def test_ctrl_c(self) -> None:
        cmd = convert_step_to_cline_command("2. press ctrl+c")
        joined = " ".join(cmd)
        assert "kill" in joined


class TestExtractSteps:
    def test_parses_github_markdown(self) -> None:
        body = """### Steps to reproduce
1. open cline
2. press `ctrl+c`
3. raises core dumped error

### What happened"""
        steps = parse_steps_from_issue_body(body)
        assert len(steps) == 3
        assert "open cline" in steps[0]

    def test_empty_body(self) -> None:
        assert parse_steps_from_issue_body("") == []


REAL_ISSUE_URL = "https://github.com/cline/cline/issues/11761"


class TestReviewRealIssueCrashSim:
    @pytest.mark.asyncio
    async def test_parses_and_detects_crash(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_payload(
            title="core dumped when trying to exit",
            body=(
                "### Cline Version\n3.0.29\n\n"
                "### Steps to reproduce\n"
                "1. open cline\n"
                "2. press `ctrl+c`\n"
                "3. raises `core dumped` error. idk if this is `bun` fault or `Cline`\n\n"
                "### What happened?\n"
                "it happened 5 times when trying to exit TUI Cline.\n"
            ),
            state=IssueState.OPEN,
        )
        provider = CrashSimProvider()
        reviewer = IssueReviewer(provider=provider, fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)

        assert result.success is True
        assert result.reproduced is True
        assert result.crash_observed is True, f"Expected crash_observed=True, got {result}"
        assert "CONFIRMED" in result.verdict
        assert any(s.get("crashed") for s in result.steps_executed)
        assert "crash-sim-1" not in provider._boxes

    @pytest.mark.asyncio
    async def test_parses_title(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_payload(title="core dumped when trying to exit", body="", state=IssueState.OPEN)
        provider = CrashSimProvider()
        reviewer = IssueReviewer(provider=provider, fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)
        assert "core dumped when trying to exit" in result.issue_title

    @pytest.mark.asyncio
    async def test_crash_step_has_core_dump(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_payload(
            title="crash bug",
            body="### Steps to reproduce\n1. open cline\n2. press ctrl+c\n",
            state=IssueState.OPEN,
        )
        provider = CrashSimProvider()
        reviewer = IssueReviewer(provider=provider, fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)

        crashed = [s for s in result.steps_executed if s.get("crashed")]
        assert len(crashed) >= 1
        assert crashed[0]["exit_code"] == 139
        assert "core.cline" in crashed[0].get("core_dump", "")

    @pytest.mark.asyncio
    async def test_no_crash_when_all_steps_ok(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_payload(
            title="ok issue",
            body="### Steps to reproduce\n1. open cline\n2. do something\n",
            state=IssueState.OPEN,
        )
        reviewer = IssueReviewer(provider=HappyProvider(), fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)
        assert result.success is True
        assert result.reproduced is True
        assert result.crash_observed is False

    @pytest.mark.asyncio
    async def test_sandbox_destroyed_on_fetch_error(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_error(ValueError("bad url"))
        reviewer = IssueReviewer(provider=HappyProvider(), fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)
        assert result.success is False
        assert "fetch error" in result.issue_title

    @pytest.mark.asyncio
    async def test_review_many(self) -> None:
        fetcher = FakeGitHubIssueFetcher()
        for i in range(3):
            fetcher.set_payload(title=f"issue {i}", body="", state=IssueState.OPEN)
        reviewer = IssueReviewer(provider=HappyProvider(), fetcher=fetcher)
        results = await reviewer.review_many([REAL_ISSUE_URL] * 3, max_concurrent=2)
        assert len(results) == 3
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_write_report(self, tmp_path: Path) -> None:
        fetcher = FakeGitHubIssueFetcher()
        fetcher.set_payload(title="test", body="body", state=IssueState.OPEN)
        reviewer = IssueReviewer(provider=HappyProvider(), fetcher=fetcher)
        result = await reviewer.review(REAL_ISSUE_URL)
        out = tmp_path / "findings.md"
        written = reviewer.write_report(result, output_path=out)
        assert written.exists()
        content = written.read_text()
        assert REAL_ISSUE_URL in content
        assert "test" in content


class TestReviewResult:
    def test_defaults(self) -> None:
        r = ReviewResult(issue_url="x", issue_title="t", sandbox_id="b", success=False)
        assert not r.crash_observed
        assert r.verdict == ""
