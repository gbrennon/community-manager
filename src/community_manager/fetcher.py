from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from community_manager.issue import Issue
from community_manager.issue_state import IssueState

_GITHUB_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)/?$"
)

_API_URL_FMT = "https://api.github.com/repos/{owner}/{repo}/issues/{number}"


class GitHubIssueFetcher:
    """Fetches a single GitHub issue from the public REST API."""

    def fetch(self, url: str) -> Issue:
        """Fetch a GitHub issue from a public repo.

        Args:
            url: The full GitHub issue URL, e.g.
                https://github.com/cline/cline/issues/11761

        Returns:
            An Issue instance populated from the API response.

        Raises:
            ValueError: If the URL cannot be parsed.
            urllib.error.HTTPError: If the API request fails.
        """
        owner, repo, number = self._parse_url(url)
        api_url = _API_URL_FMT.format(owner=owner, repo=repo, number=number)
        data = self._get_json(api_url)
        return self._build_issue(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str, int]:
        m = _GITHUB_ISSUE_URL_RE.match(url.strip().rstrip("/"))
        if not m:
            raise ValueError(f"Invalid GitHub issue URL: {url!r}")
        return m.group("owner"), m.group("repo"), int(m.group("number"))

    def _get_json(self, api_url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            api_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "community-manager"},
            method="GET",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())  # type: ignore[no-any-return]

    @staticmethod
    def _build_issue(data: dict[str, Any]) -> Issue:
        title: str = data.get("title") or ""
        body: str = data.get("body") or ""
        state: str = data.get("state", "open")
        state_reason: str | None = data.get("state_reason")
        labels: list[dict[str, Any]] = data.get("labels", [])

        issue_state = GitHubIssueFetcher._determine_state(state, state_reason, labels)
        return Issue(title=title, body=body, state=issue_state)

    @staticmethod
    def _determine_state(
        state: str,
        state_reason: str | None,
        labels: list[dict[str, Any]],
    ) -> IssueState:
        if state != "closed":
            return IssueState.OPEN

        # GitHub currently exposes state_reason: completed | not_planned | reopened
        # Duplicate detection relies on a "duplicate" label (case-insensitive).
        label_names = {label.get("name", "").lower() for label in labels}
        if "duplicate" in label_names:
            return IssueState.CLOSED_DUPLICATE

        if state_reason == "not_planned":
            return IssueState.CLOSED_AS_NOT_PLANNED

        # Default for closed issues (covers "completed" and older issues
        # that predate the state_reason field).
        return IssueState.CLOSED_AS_COMPLETED
