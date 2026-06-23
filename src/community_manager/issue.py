from dataclasses import dataclass

from community_manager.issue_state import IssueState


@dataclass
class Issue:
    """Represents a GitHub issue with title, body, and state."""

    title: str
    body: str
    state: IssueState
