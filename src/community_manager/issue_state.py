from enum import StrEnum


class IssueState(StrEnum):
    """Represents the state of a GitHub issue."""

    OPEN = "Open"
    CLOSED_AS_COMPLETED = "ClosedAsCompleted"
    CLOSED_AS_NOT_PLANNED = "ClosedAsNotPlanned"
    CLOSED_DUPLICATE = "ClosedDuplicate"
