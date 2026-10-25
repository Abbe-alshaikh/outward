"""Domain value objects passed between the layers.

Replacing pandas rows and positional tuples with named, immutable objects
documents the data contract and keeps pandas confined to the database adapter.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

STATUS_OK = "002"
STATUS_FAILED = "003"


class Lob(Protocol):
    """The subset of ``oracledb.LOB`` this service uses."""

    def getchunksize(self) -> int: ...

    def size(self) -> int: ...

    def read(self, offset: int, amount: int) -> bytes | str: ...


@dataclass(frozen=True)
class PendingTask:
    institution_number: str
    integration_name: str
    process_id: Any


@dataclass(frozen=True)
class TaskPayload:
    outward_filename: Path
    outward_data: Lob | None
    archive_filename: str | None = None
    archive_data: Lob | None = None
    successor: str | None = None


@dataclass(frozen=True)
class SuccessorResult:
    file_count: int = 0
    file_size: int = 0
    completion_status: str = STATUS_OK
    additional_data: str = ""


@dataclass(frozen=True)
class TaskResult:
    file_name: Path
    file_count: int
    file_size: int
    completion_status: str = STATUS_OK
    additional_data: str = ""

    def with_successor(self, successor: SuccessorResult) -> "TaskResult":
        """Fold a successor's output into this result.

        The successor's status and additional data always win, which matches
        the original behaviour.
        """
        return replace(
            self,
            file_count=self.file_count + successor.file_count,
            file_size=self.file_size + successor.file_size,
            completion_status=successor.completion_status,
            additional_data=successor.additional_data,
        )
