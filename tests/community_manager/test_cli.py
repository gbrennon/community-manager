from __future__ import annotations

import pytest

from community_manager.cli import _normalize_repo, _resolve_targets_to_urls, build_parser, run
from community_manager.issue_state import IssueState
from tests.conftest import FakeGitHubIssueFetcher


class TestBuildParser:
    def test_program_name(self) -> None:
        parser = build_parser()
        assert parser.prog == "communiy-manager"

    def test_fetch_url(self) -> None:
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
        assert "batch" in output


class TestRun:
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

    def test_run_with_empty_body(
        self, fake_fetcher: FakeGitHubIssueFetcher, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_fetcher.set_payload(
            title="Empty body issue", body="", state=IssueState.OPEN,
        )
        run(["https://github.com/a/b/issues/1"], fetcher=fake_fetcher)
        assert "(empty body)" in capsys.readouterr().out


class TestNormalizeRepo:
    def test_already_short_form(self) -> None:
        assert _normalize_repo("cline/cline") == "cline/cline"

    def test_https_github_url(self) -> None:
        assert _normalize_repo("https://github.com/cline/cline") == "cline/cline"

    def test_https_github_url_with_trailing_slash(self) -> None:
        assert _normalize_repo("https://github.com/cline/cline/") == "cline/cline"

    def test_http_github_url(self) -> None:
        assert _normalize_repo("http://github.com/cline/cline") == "cline/cline"

    def test_github_dot_com_no_scheme(self) -> None:
        assert _normalize_repo("github.com/cline/cline") == "cline/cline"

    def test_trailing_slash_on_short_form(self) -> None:
        assert _normalize_repo("cline/cline/") == "cline/cline"


class TestResolveTargetsToUrls:
    def test_full_urls_passed_through(self) -> None:
        urls = ["https://github.com/cline/cline/issues/1"]
        assert _resolve_targets_to_urls(urls, None) == urls

    def test_bare_ids_with_short_repo(self) -> None:
        result = _resolve_targets_to_urls(["11761", "11762"], "cline/cline")
        assert result == [
            "https://github.com/cline/cline/issues/11761",
            "https://github.com/cline/cline/issues/11762",
        ]

    def test_bare_ids_with_full_repo_url(self) -> None:
        """--repo https://github.com/cline/cline should work the same as cline/cline."""
        result = _resolve_targets_to_urls(["11761", "11762"], "https://github.com/cline/cline")
        assert result == [
            "https://github.com/cline/cline/issues/11761",
            "https://github.com/cline/cline/issues/11762",
        ]

    def test_bare_id_without_repo_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _resolve_targets_to_urls(["11761"], None)
        assert exc_info.value.code == 1

    def test_mix_of_urls_and_ids(self) -> None:
        result = _resolve_targets_to_urls(
            ["https://github.com/cline/cline/issues/100", "200"],
            "https://github.com/cline/cline",
        )
        assert result == [
            "https://github.com/cline/cline/issues/100",
            "https://github.com/cline/cline/issues/200",
        ]
