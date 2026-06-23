from community_manager.issue_state import IssueState


class TestIssueState:
    """Unit tests for the IssueState StrEnum."""

    def test_enum_members(self) -> None:
        assert set(IssueState) == {
            IssueState.OPEN,
            IssueState.CLOSED_AS_COMPLETED,
            IssueState.CLOSED_AS_NOT_PLANNED,
            IssueState.CLOSED_DUPLICATE,
        }

    def test_values_are_strings(self) -> None:
        assert IssueState.OPEN.value == "Open"
        assert IssueState.CLOSED_AS_COMPLETED.value == "ClosedAsCompleted"
        assert IssueState.CLOSED_AS_NOT_PLANNED.value == "ClosedAsNotPlanned"
        assert IssueState.CLOSED_DUPLICATE.value == "ClosedDuplicate"

    def test_strenum_equality_with_str(self) -> None:
        assert IssueState.OPEN == "Open"
        assert IssueState.CLOSED_DUPLICATE == "ClosedDuplicate"
