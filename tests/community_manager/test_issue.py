from community_manager.issue import Issue
from community_manager.issue_state import IssueState


class TestIssue:
    """Unit tests for the Issue dataclass."""

    def test_construction(self) -> None:
        issue = Issue(
            title="Something is broken",
            body="Steps to reproduce...",
            state=IssueState.OPEN,
        )
        assert issue.title == "Something is broken"
        assert issue.body == "Steps to reproduce..."
        assert issue.state == IssueState.OPEN

    def test_equality(self) -> None:
        a = Issue(title="t", body="b", state=IssueState.CLOSED_AS_COMPLETED)
        b = Issue(title="t", body="b", state=IssueState.CLOSED_AS_COMPLETED)
        assert a == b

    def test_inequality(self) -> None:
        a = Issue(title="t", body="b", state=IssueState.OPEN)
        b = Issue(title="t", body="b", state=IssueState.CLOSED_DUPLICATE)
        assert a != b
