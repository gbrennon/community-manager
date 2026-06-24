from dataclasses import dataclass, field

from community_manager.issue_state import IssueState


@dataclass
class Issue:
    """Represents a GitHub issue with title, body, state, and cline version."""

    title: str
    body: str
    state: IssueState
    cline_version: str = ""
