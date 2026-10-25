from datetime import date

import pytest

from outward.config import Settings
from outward.errors import DatabaseError
from outward.models import STATUS_FAILED, PendingTask, SuccessorResult, TaskPayload
from outward.processor import TaskProcessor
from tests.fakes import FakeLob, settings_dict

TASK = PendingTask("00000051", "OUT_X", 7)


class FakeStore:
    """Records completions and what was visible on disk at that moment."""

    def __init__(self, payload, fail_complete=False):
        self.payload = payload
        self.fail_complete = fail_complete
        self.completed = []

    def fetch_payload(self, task):
        return self.payload

    def complete(self, task, result):
        final = self.payload.outward_filename
        self.completed.append(
            {"result": result, "final_visible": final.exists(), "staged_exists": (final.parent / "tmp" / final.name).exists()}
        )
        if self.fail_complete:
            raise DatabaseError("ORA-03113: end-of-file on communication channel")


class FakeSuccessor:
    def __init__(self, result):
        self.result = result
        self.control_files = []

    def run(self, control_file):
        self.control_files.append((control_file, control_file.read_bytes()))
        return self.result


@pytest.fixture
def settings(tmp_path):
    return Settings.from_dict(settings_dict(tmp_path))


def processor(store, settings, successors=None):
    return TaskProcessor(store, settings, successors or {}, today=lambda: date(2024, 3, 7))


def test_file_is_published_only_after_completion_is_committed(tmp_path, settings):
    final = tmp_path / "out" / "file.txt"
    store = FakeStore(TaskPayload(final, FakeLob(b"payload")))

    result = processor(store, settings).process(TASK)

    assert store.completed[0]["final_visible"] is False  # BizTalk could not see it yet
    assert store.completed[0]["staged_exists"] is True
    assert final.read_bytes() == b"payload"
    assert not (final.parent / "tmp" / "file.txt").exists()
    assert (result.file_name, result.file_count, result.file_size, result.completion_status) == (final, 1, 7, "002")


def test_completion_failure_leaves_nothing_for_biztalk(tmp_path, settings):
    final = tmp_path / "out" / "file.txt"
    store = FakeStore(TaskPayload(final, FakeLob(b"payload")), fail_complete=True)

    with pytest.raises(DatabaseError):
        processor(store, settings).process(TASK)

    assert not final.exists()


def test_archive_copy_goes_into_dated_folder(tmp_path, settings):
    final = tmp_path / "out" / "file.txt"
    payload = TaskPayload(final, FakeLob(b"payload"), archive_filename="a.txt", archive_data=FakeLob(b"archive"))

    processor(FakeStore(payload), settings).process(TASK)

    assert (settings.archive_root / "2024" / "03" / "07" / "a.txt").read_bytes() == b"archive"


def test_archive_with_null_data_writes_empty_file(tmp_path, settings):
    # Regression: the original crashed with AttributeError on None.getchunksize().
    payload = TaskPayload(tmp_path / "f.txt", FakeLob(b"x"), archive_filename="a.txt", archive_data=None)
    processor(FakeStore(payload), settings).process(TASK)
    assert (settings.archive_root / "2024" / "03" / "07" / "a.txt").read_bytes() == b""


def test_successor_runs_on_staged_file_and_its_result_is_merged(tmp_path, settings):
    final = tmp_path / "out" / "manifest.txt"
    store = FakeStore(TaskPayload(final, FakeLob(b"rows"), successor="HCSV"))
    successor = FakeSuccessor(SuccessorResult(2, 100, STATUS_FAILED, "info"))

    result = processor(store, settings, {"hcsv": successor}).process(TASK)

    assert successor.control_files == [(final.parent / "tmp" / "manifest.txt", b"rows")]
    assert (result.file_count, result.file_size, result.completion_status, result.additional_data) == (3, 104, "003", "info")
    assert store.completed[0]["result"] == result


def test_unknown_successor_is_ignored_with_a_warning(tmp_path, settings, caplog):
    store = FakeStore(TaskPayload(tmp_path / "f.txt", FakeLob(b"x"), successor="hxml"))
    result = processor(store, settings).process(TASK)
    assert result.completion_status == "002" and result.file_count == 1
    assert "Unknown successor type 'hxml'" in caplog.text
