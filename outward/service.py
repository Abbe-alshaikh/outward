"""Batch run and the error-to-exit-code policy."""

import logging
from collections.abc import Callable
from typing import Protocol

from outward.config import Settings
from outward.errors import EX_SOFTWARE, OutwardError
from outward.models import PendingTask
from outward.processor import TaskProcessor
from outward.storage import verify_mounts

logger = logging.getLogger("okq8")

EX_OK = 0


class TaskQueue(Protocol):
    def register_container_startup(self) -> None: ...

    def pending_tasks(self) -> list[PendingTask]: ...


def run_batch(queue: TaskQueue, processor: TaskProcessor, settings: Settings) -> int:
    """Process every pending task in order and return how many were processed.

    The batch is fail-fast: the first failing task stops the run. Integrations
    such as Trapets are serial, so later tasks must not overtake a failed one.
    The failed task is released by container_startup on the next run and
    retried up to ``retry_limit`` times.
    """
    verify_mounts(settings.mount_checks)
    queue.register_container_startup()

    tasks = queue.pending_tasks()
    logger.info(f"{len(tasks)} outward tasks are pending")
    for task in tasks:
        processor.process(task)

    logger.info("Finished processing all pending tasks")
    return len(tasks)


def exit_code(action: Callable[[], object]) -> int:
    """Run ``action`` and translate the outcome into a process exit code."""
    try:
        action()
    except OutwardError as e:
        logger.error(f"{e} - Exiting")
        return e.exit_code
    except Exception:
        logger.exception("Unexpected error - Exiting")
        return EX_SOFTWARE
    return EX_OK
