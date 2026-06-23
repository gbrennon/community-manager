from __future__ import annotations

from typing import Any

import pytest

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.issue import Issue
from community_manager.issue_state import IssueState


class FakeGitHubIssueFetcher(GitHubIssueFetcher):
    """A test fake that returns canned API responses without touching the network.

    Call ``set_payload(...)`` or ``set_error(...)`` before exercising the
    code under test.
    """

    def __init__(self) -> None:
        super().__init__()
        self._payloads: list[dict[str, Any]] = []
        self._errors: list[Exception] = []

    # -- Test-facing API -----------------------------------------------------

    def set_payload(self, *, title: str, body: str, state: IssueState) -> None:
        """Queue a payload so the next ``fetch()`` returns the given Issue."""
        reason: str | None = None
        labels: list[dict[str, Any]] = []

        if state == IssueState.CLOSED_AS_COMPLETED:
            api_state = "closed"
            reason = "completed"
        elif state == IssueState.CLOSED_AS_NOT_PLANNED:
            api_state = "closed"
            reason = "not_planned"
        elif state == IssueState.CLOSED_DUPLICATE:
            api_state = "closed"
            reason = "completed"
            labels = [{"name": "duplicate"}]
        else:
            api_state = "open"

        self._payloads.append(
            {
                "title": title,
                "body": body,
                "state": api_state,
                "state_reason": reason,
                "labels": labels,
            }
        )

    def set_error(self, exc: Exception) -> None:
        """Queue an exception so the next ``fetch()`` raises it."""
        self._errors.append(exc)

    # -- Overrides (bypass the network) --------------------------------------

    def fetch(self, url: str) -> Issue:
        # Delegate URL parsing to the *real* implementation.
        owner, repo, number = self._parse_url(url)
        # If an error was queued, raise it.
        if self._errors:
            raise self._errors.pop(0)
        data = self._payloads.pop(0)
        return self._build_issue(data)

    def _get_json(self, api_url: str) -> dict[str, Any]:
        # Never called because fetch() is fully overridden.
        raise NotImplementedError("Fake fetcher should not call _get_json")


@pytest.fixture
def fake_fetcher() -> FakeGitHubIssueFetcher:
    """Return a pre-configured FakeGitHubIssueFetcher instance."""
    return FakeGitHubIssueFetcher()
