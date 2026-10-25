"""Processing of a single outward task."""

import logging
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Protocol

from outward.config import Settings
from outward.models import PendingTask, SuccessorResult, TaskPayload, TaskResult
from outward.storage import archive_path, publish, staging_path, write_lob
from outward.successors import Successor

logger = logging.getLogger("okq8")


class TaskStore(Protocol):
    """What the processor needs from the repository (see TaskRepository)."""

    def fetch_payload(self, task: PendingTask) -> TaskPayload: ...

    def complete(self, task: PendingTask, result: TaskResult) -> None: ...


class TaskProcessor:
    """Turns one pending task into files on disk and a completed database record.

    Ordering is the key design decision here:

    1. write the outward file to ``tmp/`` (invisible to BizTalk)
    2. write the archive copy and run any successor
    3. commit completion in the database
    4. only then move the outward file into the folder BizTalk polls

    If the process dies before step 3, the task is reset by container_startup
    and retried, and BizTalk has seen nothing. The original published before
    step 2, so a crash in steps 2-3 delivered the file *and* retried the task,
    which is a duplicate delivery downstream.
    """

    def __init__(
        self,
        store: TaskStore,
        settings: Settings,
        successors: Mapping[str, Successor],
        today: Callable[[], date] = date.today,
    ):
        self.store = store
        self.settings = settings
        self.successors = successors
        self.today = today

    def process(self, task: PendingTask) -> TaskResult:
        logger.info(f"Started {task.integration_name} with process_id {task.process_id}")
        payload = self.store.fetch_payload(task)

        final = payload.outward_filename
        staged = staging_path(final)
        size = self._write(staged, payload.outward_data)
        result = TaskResult(file_name=final, file_count=1, file_size=size)

        if payload.archive_filename:
            target = archive_path(self.settings.archive_root, payload.archive_filename, self.today())
            self._write(target, payload.archive_data)

        if payload.successor:
            result = result.with_successor(self._run_successor(payload.successor, staged))

        self.store.complete(task, result)
        publish(staged, final)

        logger.info(f"File name: {result.file_name}")
        logger.info(f"File count: {result.file_count}")
        logger.info(f"File size: {result.file_size}")
        logger.info(f"Completion status: {result.completion_status}")
        logger.info(f"Additional_data: {result.additional_data}")
        logger.info(f"Process_id: {task.process_id} completed successfully")
        return result

    def _write(self, path: Path, lob) -> int:
        return write_lob(path, lob, self.settings.chunks, self.settings.clob_encoding)

    def _run_successor(self, kind: str, control_file: Path) -> SuccessorResult:
        successor = self.successors.get(kind.strip().lower())
        if successor is None:
            logger.warning(f"Unknown successor type {kind!r} - ignored")
            return SuccessorResult()
        return successor.run(control_file)
