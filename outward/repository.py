"""Repository over the BankWorks stored procedures.

This is the only module that knows about oracledb, pandas DataFrames, column
names and procedure parameter shapes. Everything it returns is a domain object
from ``outward.models``, so the rest of the service can be tested without a
database.
"""

import logging
import math
from pathlib import Path
from typing import Any, Protocol

import oracledb
from pandas import DataFrame

from outward.config import Settings
from outward.errors import DatabaseError
from outward.models import PendingTask, TaskPayload, TaskResult

logger = logging.getLogger("okq8")

# Value passed to update_biztalk_log_ext identifying this integration family.
COMPLETION_SOURCE = "BDH_OUTWARD"


class BankworksClient(Protocol):
    """The subset of ``okq8common.bankworks.db.Bankworks`` this service uses."""

    curBw3: Any
    dbConn: Any

    def callproc(self, procedure: str, parameters: dict) -> Any: ...

    def callproc_returning_cursor(self, procedure: str, parameters: dict, cursor_field: int) -> Any: ...


class TaskRepository:
    def __init__(self, bw3: BankworksClient, settings: Settings):
        self.bw3 = bw3
        self.settings = settings
        self.procs = settings.procedures

    def register_container_startup(self) -> None:
        """Release tasks this container locked but never completed (e.g. after a crash)."""
        ok = self.bw3.callproc(
            procedure=self.procs.qualified(self.procs.container_startup),
            parameters=dict(
                container_id=self.settings.container_id,
                retry_limit=self.settings.retry_limit,
            ),
        )
        if not ok:
            raise DatabaseError("Failed to set container startup")

    def pending_tasks(self) -> list[PendingTask]:
        tasks = self.bw3.callproc_returning_cursor(
            procedure=self.procs.qualified(self.procs.get_pending_tasks),
            parameters=dict(
                p_institution_number=self.settings.institution_number,
                data=self.bw3.curBw3.var(oracledb.CURSOR),
                container_id=self.settings.container_id,
            ),
            cursor_field=1,
        )
        if not isinstance(tasks, DataFrame):
            raise DatabaseError("Failed to get pending tasks")
        return [
            PendingTask(
                institution_number=row["INSTITUTION_NUMBER"],
                integration_name=row["INTEGRATION_NAME"],
                process_id=row["PROCESS_ID"],
            )
            for _, row in tasks.iterrows()
        ]

    def fetch_payload(self, task: PendingTask) -> TaskPayload:
        try:
            # INTEGRATION_NAME comes from the database and ends up inside PL/SQL.
            procedure = self.procs.qualified(task.integration_name)
        except ValueError as e:
            raise DatabaseError(f"Task {task.process_id} has an invalid integration name: {e}") from e

        output = self.bw3.callproc_returning_cursor(
            procedure=procedure,
            parameters=dict(
                institution_number=task.institution_number,
                process_id=task.process_id,
                data=self.bw3.curBw3.var(oracledb.CURSOR),
            ),
            cursor_field=2,
        )
        if not isinstance(output, DataFrame) or output.empty:
            raise DatabaseError(f"Failed to get task output for {task.process_id}")
        if len(output) > 1:
            logger.warning(f"Task {task.process_id} returned {len(output)} rows; only the first is used")

        row = output.iloc[0]
        outward_filename = _optional(row.get("OUTWARD_FILENAME"))
        if outward_filename is None:
            raise DatabaseError(f"Task {task.process_id} has no OUTWARD_FILENAME")

        return TaskPayload(
            outward_filename=Path(outward_filename),
            outward_data=row.get("OUTWARD_DATA"),
            archive_filename=_optional(row.get("ARCHIVE_FILENAME")),
            archive_data=row.get("ARCHIVE_DATA"),
            successor=_optional(row.get("OUTWARD_SUCCESSOR")),
        )

    def complete(self, task: PendingTask, result: TaskResult) -> None:
        """Flag the task complete. Commits the work done by the task procedure first."""
        parameters = (
            task.process_id,
            result.file_count,  # files expected
            result.file_count,  # files written
            result.completion_status,
            task.integration_name,
            COMPLETION_SOURCE,
            result.file_name.name,
            result.file_size,
            result.additional_data,
            "",
            "",
        )
        try:
            # Serial integrations (e.g. Trapets) need the task procedure's work
            # committed before the completion call.
            self.bw3.dbConn.commit()
            self.bw3.curBw3.callproc(self.procs.qualified(self.procs.complete_task), parameters)
            self.bw3.dbConn.commit()
        except oracledb.Error as e:
            self.bw3.dbConn.rollback()
            raise DatabaseError(f"Failed to complete task {task.process_id}: {e}") from e

    def run_query(self, sql: str) -> Any:
        """Execute ad-hoc successor SQL and return the open cursor."""
        cursor = self.bw3.curBw3
        cursor.arraysize = self.settings.array_size
        try:
            cursor.execute(sql)
        except oracledb.Error as e:
            raise DatabaseError(f"Successor query failed: {e}") from e
        return cursor


def _optional(value: Any) -> str | None:
    """Normalise NULL-ish column values (None, NaN, blank) to None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value)
    return text if text.strip() else None
