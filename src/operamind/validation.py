"""Shared validation result types."""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """Severity of a baseline validation issue."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable validation failure or readiness warning."""

    code: str
    message: str
    location: str
    severity: Severity = Severity.ERROR


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Immutable collection of baseline validation issues."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return whether the report contains no error-level issue."""

        return not any(issue.severity is Severity.ERROR for issue in self.issues)
