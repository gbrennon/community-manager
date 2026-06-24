from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from community_manager.issue import Issue
from community_manager.issue_state import IssueState

_GITHUB_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)/?$"
)

_API_URL_FMT = "https://api.github.com/repos/{owner}/{repo}/issues/{number}"

_CLINE_VERSION_SECTION_TITLE = re.compile(r"### Cline Version", re.IGNORECASE)
_CLINE_VERSION_VALUE = re.compile(r"(\d+\.\d+\.\d+)")


class GitHubIssueFetcher:
    """Fetches a single GitHub issue from the public REST API."""

    def fetch(self, url: str) -> Issue:
        owner, repo, number = self._parse_url(url)
        api_url = _API_URL_FMT.format(owner=owner, repo=repo, number=number)
        data = self._get_json(api_url)
        return self._build_issue(data)

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
        cline_version = GitHubIssueFetcher._extract_cline_version(body)
        return Issue(title=title, body=body, state=issue_state, cline_version=cline_version)

    @staticmethod
    def _extract_cline_version(body: str) -> str:
        version_section = _CLINE_VERSION_SECTION_TITLE.split(body, maxsplit=1)
        if len(version_section) < 2:
            return ""
        after_heading = version_section[1]
        match = _CLINE_VERSION_VALUE.search(after_heading)
        return match.group(1) if match else ""

    @staticmethod
    def _determine_state(
        state: str,
        state_reason: str | None,
        labels: list[dict[str, Any]],
    ) -> IssueState:
        if state != "closed":
            return IssueState.OPEN

        label_names = {label.get("name", "").lower() for label in labels}
        if "duplicate" in label_names:
            return IssueState.CLOSED_DUPLICATE

        if state_reason == "not_planned":
            return IssueState.CLOSED_AS_NOT_PLANNED

        return IssueState.CLOSED_AS_COMPLETED
