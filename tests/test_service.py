from datetime import date

import pytest
from pandas import DataFrame

from outward.config import Settings
from outward.errors import EX_CONFIG, EX_IOERR, EX_OSFILE, EX_SOFTWARE, ConfigError, DatabaseError
from outward.models import PendingTask
from outward.processor import TaskProcessor
from outward.repository import TaskRepository
from outward.service import EX_OK, exit_code, run_batch
from outward.successors import build_successors
from tests.fakes import FakeBankworks, FakeLob, settings_dict


class FakeQueue:
    def __init__(self, tasks):
        self.tasks = tasks
        self.calls = []

    def register_container_startup(self):
        self.calls.append("startup")

    def pending_tasks(self):
        self.calls.append("pending")
        return self.tasks


class FakeProcessor:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.processed = []

    def process(self, task):
        if task.process_id == self.fail_on:
            raise DatabaseError(f"task {task.process_id} failed")
        self.processed.append(task.process_id)


@pytest.fixture
def settings(tmp_path):
    return Settings.from_dict(settings_dict(tmp_path))


def tasks(*ids):
    return [PendingTask("1", "OUT_X", i) for i in ids]


def test_processes_all_tasks_in_order(settings):
    queue, proc = FakeQueue(tasks(1, 2, 3)), FakeProcessor()
    assert run_batch(queue, proc, settings) == 3
    assert queue.calls == ["startup", "pending"]
    assert proc.processed == [1, 2, 3]


def test_mount_failure_stops_before_touching_the_database(tmp_path, settings):
    settings.mount_checks[0].unlink()
    queue = FakeQueue(tasks(1))
    assert exit_code(lambda: run_batch(queue, FakeProcessor(), settings)) == EX_OSFILE
    assert queue.calls == []


def test_batch_is_fail_fast_to_preserve_order(settings):
    proc = FakeProcessor(fail_on=2)
    assert exit_code(lambda: run_batch(FakeQueue(tasks(1, 2, 3)), proc, settings)) == EX_IOERR
    assert proc.processed == [1]


@pytest.mark.parametrize(
    "error,code",
    [(None, EX_OK), (ConfigError("x"), EX_CONFIG), (DatabaseError("x"), EX_IOERR), (KeyError("x"), EX_SOFTWARE)],
)
def test_exit_codes(error, code):
    def action():
        if error:
            raise error

    assert exit_code(action) == code


def test_end_to_end_with_real_components_and_fake_database(tmp_path, settings):
    """Wires the real repository, processor and CSV successor the way app.py does."""
    out = tmp_path / "out"
    manifest = f"TABLE§bw3.export_view§x§{out / 'exports'}§data.csv§2§batch-1\n".encode("cp1252")
    pending = DataFrame([{"INSTITUTION_NUMBER": "00000051", "INTEGRATION_NAME": "OUT_EXPORT", "PROCESS_ID": 99}])
    task_output = DataFrame(
        [
            {
                "OUTWARD_FILENAME": str(out / "manifest.txt"),
                "OUTWARD_DATA": FakeLob(manifest),
                "ARCHIVE_FILENAME": "manifest.txt",
                "ARCHIVE_DATA": FakeLob(manifest),
                "OUTWARD_SUCCESSOR": "HCSV",
            }
        ]
    )
    bw = FakeBankworks(returns=[True, pending, task_output])
    bw.curBw3.description = [("ID",), ("NAME",)]
    bw.curBw3.rows = [(1, "a"), (2, "b")]

    repository = TaskRepository(bw, settings)
    processor = TaskProcessor(repository, settings, build_successors(repository, settings), today=lambda: date(2024, 1, 2))

    assert exit_code(lambda: run_batch(repository, processor, settings)) == EX_OK

    assert (out / "manifest.txt").read_bytes() == manifest
    assert (out / "exports" / "data.csv").read_text() == "ID\tNAME\n1\ta\n2\tb\n"
    assert (settings.archive_root / "2024" / "01" / "02" / "manifest.txt").exists()
    assert bw.curBw3.executed == ["select * from bw3.export_view"]

    completion = [entry for entry in bw.log if entry[0] == "callproc" and entry[1].endswith("update_biztalk_log_ext")]
    (_, _, params), = completion
    csv_size = (out / "exports" / "data.csv").stat().st_size
    assert params == (99, 2, 2, "002", "OUT_EXPORT", "BDH_OUTWARD", "manifest.txt", len(manifest) + csv_size, "batch-1", "", "")
