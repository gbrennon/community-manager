from __future__ import annotations

import pytest

from community_manager.cli import build_parser, run
from community_manager.issue_state import IssueState
from tests.conftest import FakeGitHubIssueFetcher


class TestBuildParser:
    """Tests for the argument parser builder."""

    def test_program_name_and_description(self) -> None:
        parser = build_parser()
        assert parser.prog == "communiy-manager"
        assert "Cline" in parser.description or ""

    def test_url_argument_present(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["fetch", "https://github.com/a/b/issues/1"])
        assert args.url == "https://github.com/a/b/issues/1"

    def test_help_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "communiy-manager" in output
        assert "review" in output
        assert "fetch" in output


class TestRun:
    """Tests for the CLI run() function using the fake fetcher."""

    def test_run_success(
        self, fake_fetcher: FakeGitHubIssueFetcher, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_fetcher.set_payload(
            title="Fixed crash",
            body="The bug is gone.",
            state=IssueState.CLOSED_AS_COMPLETED,
        )
        run(["https://github.com/a/b/issues/1"], fetcher=fake_fetcher)

        out = capsys.readouterr().out
        assert "Title: Fixed crash" in out
        assert "State: ClosedAsCompleted" in out
        assert "The bug is gone." in out

    def test_run_error(
        self, fake_fetcher: FakeGitHubIssueFetcher, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_fetcher.set_error(ValueError("Something went wrong"))

        with pytest.raises(SystemExit) as exc_info:
            run(["https://github.com/a/b/issues/1"], fetcher=fake_fetcher)

        assert exc_info.value.code == 1
        assert "Error: Something went wrong" in capsys.readouterr().err

    def test_run_with_empty_body_prints_placeholder(
        self, fake_fetcher: FakeGitHubIssueFetcher, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_fetcher.set_payload(
            title="Empty body issue",
            body="",
            state=IssueState.OPEN,
        )
        run(["https://github.com/a/b/issues/1"], fetcher=fake_fetcher)

        assert "(empty body)" in capsys.readouterr().out

