"""Successors: optional post-processing driven by the outward file itself.

``OUTWARD_SUCCESSOR`` on a task selects one strategy:

* ``hshl``: the outward file is a shell script and is executed with ``sh``.
* ``hcsv``: the outward file is a manifest; each row's query is exported as a
  tab-separated file.
* ``hbin``: the outward file is a manifest; each row's query returns one LOB
  that is written as-is.

Manifest rows are ``§``-separated::

    KIND § TABLE_VIEW_OR_SQL § (unused) § DIRECTORY § FILENAME [§ STATUS [§ ADDITIONAL_DATA]]
"""

import csv
import logging
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from outward.config import Settings
from outward.errors import SuccessorError
from outward.models import STATUS_FAILED, STATUS_OK, SuccessorResult
from outward.storage import staged_file, write_lob

logger = logging.getLogger("okq8")

MANIFEST_ENCODING = "cp1252"
MANIFEST_SEPARATOR = "§"


class QueryRunner(Protocol):
    """Executes ad-hoc SQL and returns an open cursor (implemented by TaskRepository)."""

    def run_query(self, sql: str) -> Any: ...


class Successor(Protocol):
    def run(self, control_file: Path) -> SuccessorResult: ...


@dataclass(frozen=True)
class ManifestRow:
    sql: str
    output_file: Path
    completion_status: str = STATUS_OK
    additional_data: str = ""

    @classmethod
    def parse(cls, line: str) -> "ManifestRow":
        columns = line.strip().split(MANIFEST_SEPARATOR)
        if len(columns) < 5:
            raise SuccessorError(
                f"Manifest row has {len(columns)} columns, expected at least 5: {line.strip()!r}"
            )
        kind, statement = columns[0].strip().upper(), columns[1]
        sql = f"select * from {statement}" if kind in ("TABLE", "VIEW") else statement
        status = columns[5].strip() if len(columns) > 5 else ""
        return cls(
            sql=sql,
            output_file=Path(columns[3]) / columns[4],
            completion_status=status.zfill(3) if status else STATUS_OK,
            additional_data=columns[6] if len(columns) > 6 else "",
        )


class ShellSuccessor:
    """Runs the outward file as a shell script.

    Success means exit code 0 *and* nothing on stderr (original contract).
    """

    def __init__(self, timeout: float | None = None, interpreter: Sequence[str] = ("sh",)):
        self.timeout = timeout
        self.interpreter = tuple(interpreter)

    def run(self, control_file: Path) -> SuccessorResult:
        try:
            proc = subprocess.run(
                [*self.interpreter, str(control_file)],
                capture_output=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Shell script {control_file} timed out after {self.timeout}s")
            return SuccessorResult(completion_status=STATUS_FAILED)
        except OSError as e:
            logger.error(f"Shell script {control_file} failed with error {e}")
            return SuccessorResult(completion_status=STATUS_FAILED)

        # errors="replace": non-UTF-8 output must not turn a successful run into a failure.
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if stdout:
            logger.info(f"{control_file} - stdout - {stdout}")
        if stderr:
            logger.error(f"{control_file} - stderr - {stderr}")

        if proc.returncode == 0 and not stderr:
            logger.info(f"Shell script {control_file} ran with return code 0")
            return SuccessorResult(completion_status=STATUS_OK)
        logger.error(f"Shell script {control_file} failed with return code {proc.returncode}")
        return SuccessorResult(completion_status=STATUS_FAILED)


class ManifestSuccessor(ABC):
    """Template for manifest-driven successors; subclasses decide how to write a cursor."""

    def __init__(self, queries: QueryRunner, settings: Settings):
        self.queries = queries
        self.settings = settings

    def run(self, control_file: Path) -> SuccessorResult:
        # Parse every row first so a malformed manifest fails before any file is written.
        try:
            lines = control_file.read_text(encoding=MANIFEST_ENCODING).splitlines()
        except OSError as e:
            raise SuccessorError(f"Cannot read successor manifest {control_file}: {e}") from e
        rows = [ManifestRow.parse(line) for line in lines if line.strip()]

        file_count, file_size = 0, 0
        status, additional_data = STATUS_OK, ""
        for row in rows:
            logger.info(f"Started file {row.output_file}")
            cursor = self.queries.run_query(row.sql)
            with staged_file(row.output_file) as staged:
                self.write(cursor, staged)
            size = row.output_file.stat().st_size
            logger.info(f"Finished file {row.output_file}. {size} bytes written")

            file_count += 1
            file_size += size
            # The last row's status wins, as in the original implementation.
            status, additional_data = row.completion_status, row.additional_data

        return SuccessorResult(file_count, file_size, status, additional_data)

    @abstractmethod
    def write(self, cursor: Any, path: Path) -> None: ...


class CsvSuccessor(ManifestSuccessor):
    """Exports a query as a tab-separated file with a header row."""

    def write(self, cursor: Any, path: Path) -> None:
        with open(path, "w", newline="") as file:
            writer = csv.writer(file, dialect="excel", delimiter="\t")
            writer.writerow(col[0] for col in cursor.description)
            while rows := cursor.fetchmany():
                writer.writerows(rows)


class BinarySuccessor(ManifestSuccessor):
    """Writes the LOB in the first column of the first row."""

    def write(self, cursor: Any, path: Path) -> None:
        row = cursor.fetchone()
        if row is None:
            raise SuccessorError(f"Query for {path.name} returned no rows")
        write_lob(path, row[0], self.settings.chunks, self.settings.clob_encoding)


def build_successors(queries: QueryRunner, settings: Settings) -> Mapping[str, Successor]:
    """Registry of successor strategies keyed by OUTWARD_SUCCESSOR (lower case)."""
    return {
        "hshl": ShellSuccessor(timeout=settings.successor_timeout),
        "hcsv": CsvSuccessor(queries, settings),
        "hbin": BinarySuccessor(queries, settings),
    }
