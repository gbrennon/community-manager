from __future__ import annotations

from typing import Any

import pytest
import vcr

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.issue import Issue
from community_manager.issue_state import IssueState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VCR = vcr.VCR(
    cassette_library_dir="tests/cassettes",
    record_mode="once",
    match_on=["uri", "method"],
    filter_headers=["authorization"],
)


# ---------------------------------------------------------------------------
# Unit tests: _parse_url
# ---------------------------------------------------------------------------

class TestParseUrl:
    """Unit tests for GitHubIssueFetcher._parse_url."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://github.com/cline/cline/issues/11761",
                ("cline", "cline", 11761),
            ),
            (
                "http://github.com/a/b/issues/1",
                ("a", "b", 1),
            ),
            (
                "https://github.com/owner/repo/issues/42/",
                ("owner", "repo", 42),
            ),
        ],
    )
    def test_valid_urls(self, url: str, expected: tuple[str, str, int]) -> None:
        assert GitHubIssueFetcher._parse_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "not-a-url",
            "https://gitlab.com/owner/repo/issues/1",
            "https://github.com/owner/issues/1",
            "https://github.com/owner/repo/pull/1",
            "https://github.com/owner/repo/issues/abc",
        ],
    )
    def test_invalid_urls_raise(self, url: str) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
            GitHubIssueFetcher._parse_url(url)


# ---------------------------------------------------------------------------
# Unit tests: _build_issue & _determine_state
# ---------------------------------------------------------------------------

class TestDetermineState:
    """Unit tests for mapping API data to IssueState."""

    @staticmethod
    def _call(
        state: str = "open",
        state_reason: str | None = None,
        labels: list[dict[str, Any]] | None = None,
    ) -> Issue:
        data: dict[str, Any] = {
            "title": "t",
            "body": "b",
            "state": state,
            "state_reason": state_reason,
            "labels": labels or [],
        }
        return GitHubIssueFetcher._build_issue(data)

    def test_open(self) -> None:
        issue = self._call(state="open")
        assert issue.state == IssueState.OPEN

    def test_closed_completed(self) -> None:
        issue = self._call(state="closed", state_reason="completed")
        assert issue.state == IssueState.CLOSED_AS_COMPLETED

    def test_closed_not_planned(self) -> None:
        issue = self._call(state="closed", state_reason="not_planned")
        assert issue.state == IssueState.CLOSED_AS_NOT_PLANNED

    def test_closed_duplicate_label(self) -> None:
        issue = self._call(
            state="closed",
            state_reason="completed",
            labels=[{"name": "duplicate"}],
        )
        assert issue.state == IssueState.CLOSED_DUPLICATE

    def test_closed_duplicate_label_case_insensitive(self) -> None:
        issue = self._call(
            state="closed",
            state_reason="not_planned",
            labels=[{"name": "Duplicate"}],
        )
        assert issue.state == IssueState.CLOSED_DUPLICATE

    def test_closed_without_state_reason_defaults_to_completed(self) -> None:
        issue = self._call(state="closed", state_reason=None)
        assert issue.state == IssueState.CLOSED_AS_COMPLETED

    def test_build_issue_preserves_title_and_body(self) -> None:
        data: dict[str, Any] = {
            "title": "My Title",
            "body": "My Body",
            "state": "open",
            "state_reason": None,
            "labels": [],
        }
        issue = GitHubIssueFetcher._build_issue(data)
        assert issue.title == "My Title"
        assert issue.body == "My Body"

    def test_body_defaults_to_empty_when_none(self) -> None:
        data: dict[str, Any] = {
            "title": "T",
            "body": None,
            "state": "open",
            "state_reason": None,
            "labels": [],
        }
        issue = GitHubIssueFetcher._build_issue(data)
        assert issue.body == ""



# ---------------------------------------------------------------------------
# Integration test: fetch() with VCR
# ---------------------------------------------------------------------------

class TestFetchIntegration:
    """Integration tests that exercise fetch() end-to-end with VCR replay."""

    def test_fetch_open_issue(self) -> None:
        url = "https://github.com/cline/cline/issues/11761"
        with _VCR.use_cassette("fetch_open_issue.yaml"):
            issue = GitHubIssueFetcher().fetch(url)

        assert isinstance(issue, Issue)
        assert issue.title
        assert issue.state == IssueState.OPEN

