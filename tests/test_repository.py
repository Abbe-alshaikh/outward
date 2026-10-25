from pathlib import Path

import oracledb
import pytest
from pandas import DataFrame

from outward.config import Settings
from outward.errors import DatabaseError
from outward.models import PendingTask, TaskResult
from outward.repository import TaskRepository
from tests.fakes import FakeCursor, FakeLob, settings_dict

TASK = PendingTask("00000051", "OUT_TRAPETS", 42)


class FakeConn:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")


class FakeBwCursor(FakeCursor):
    def __init__(self, log, fail_callproc=False, **kw):
        super().__init__(**kw)
        self.log = log
        self.fail_callproc = fail_callproc

    def var(self, kind):
        return f"<{kind.name}>"

    def callproc(self, name, params):
        if self.fail_callproc:
            raise oracledb.DatabaseError("ORA-06550")
        self.log.append(("callproc", name, params))


class FakeBankworks:
    """Records every call; returns whatever was queued in ``returns``."""

    def __init__(self, returns=None, **cursor_kw):
        self.log = []
        self.returns = list(returns or [])
        self.curBw3 = FakeBwCursor(self.log, **cursor_kw)
        self.dbConn = FakeConn(self.log)

    def callproc(self, procedure, parameters):
        self.log.append(("callproc", procedure, parameters))
        return self.returns.pop(0)

    def callproc_returning_cursor(self, procedure, parameters, cursor_field):
        self.log.append(("cursor", procedure, parameters, cursor_field))
        return self.returns.pop(0)


@pytest.fixture
def settings(tmp_path):
    return Settings.from_dict(settings_dict(tmp_path))


def test_container_startup_passes_container_and_retry_limit(settings):
    bw = FakeBankworks(returns=[True])
    TaskRepository(bw, settings).register_container_startup()
    assert bw.log == [
        (
            "callproc",
            "bw3.bw_okq8_biztalk_integration.container_startup",
            {"container_id": "outward_test", "retry_limit": 3},
        )
    ]


@pytest.mark.parametrize("returned", [False, None])
def test_container_startup_failure_raises(settings, returned):
    with pytest.raises(DatabaseError):
        TaskRepository(FakeBankworks(returns=[returned]), settings).register_container_startup()


def test_pending_tasks_are_mapped_to_domain_objects(settings):
    df = DataFrame(
        [
            {"INSTITUTION_NUMBER": "00000051", "INTEGRATION_NAME": "A", "PROCESS_ID": 1},
            {"INSTITUTION_NUMBER": "00000051", "INTEGRATION_NAME": "B", "PROCESS_ID": 2},
        ]
    )
    bw = FakeBankworks(returns=[df])
    tasks = TaskRepository(bw, settings).pending_tasks()
    assert [(t.integration_name, t.process_id) for t in tasks] == [("A", 1), ("B", 2)]
    _, proc, params, field = bw.log[0]
    assert proc.endswith(".get_pending_tasks_v2") and field == 1
    assert params["p_institution_number"] == "00000051" and params["container_id"] == "outward_test"


def test_pending_tasks_failure_raises(settings):
    with pytest.raises(DatabaseError, match="pending"):
        TaskRepository(FakeBankworks(returns=[None]), settings).pending_tasks()


def test_fetch_payload_maps_row_and_normalises_nulls(settings):
    lob = FakeLob(b"x")
    df = DataFrame(
        [
            {
                "OUTWARD_FILENAME": "/out/f.txt",
                "OUTWARD_DATA": lob,
                "ARCHIVE_FILENAME": float("nan"),
                "ARCHIVE_DATA": None,
                "OUTWARD_SUCCESSOR": "  ",
            }
        ]
    )
    bw = FakeBankworks(returns=[df])
    payload = TaskRepository(bw, settings).fetch_payload(TASK)
    assert payload.outward_filename == Path("/out/f.txt")
    assert payload.outward_data is lob
    assert payload.archive_filename is None and payload.successor is None
    _, proc, params, field = bw.log[0]
    assert proc == "bw3.bw_okq8_biztalk_integration.OUT_TRAPETS" and field == 2
    assert params["process_id"] == 42


@pytest.mark.parametrize("returned", [None, DataFrame()])
def test_fetch_payload_with_no_output_raises(settings, returned):
    # Regression: an empty DataFrame used to crash on .iloc[0] with IndexError.
    with pytest.raises(DatabaseError, match="42"):
        TaskRepository(FakeBankworks(returns=[returned]), settings).fetch_payload(TASK)


def test_fetch_payload_rejects_injected_integration_name(settings):
    bw = FakeBankworks(returns=[])
    task = PendingTask("1", "x(1); drop table t; --", 7)
    with pytest.raises(DatabaseError, match="invalid integration name"):
        TaskRepository(bw, settings).fetch_payload(task)
    assert bw.log == []  # nothing was sent to the database


def test_complete_commits_before_and_after(settings):
    bw = FakeBankworks()
    result = TaskResult(Path("/out/f.txt"), 2, 300, "002", "info")
    TaskRepository(bw, settings).complete(TASK, result)
    assert bw.log == [
        "commit",
        (
            "callproc",
            "bw3.bw_okq8_biztalk_integration.update_biztalk_log_ext",
            (42, 2, 2, "002", "OUT_TRAPETS", "BDH_OUTWARD", "f.txt", 300, "info", "", ""),
        ),
        "commit",
    ]


def test_complete_failure_rolls_back_and_raises(settings):
    bw = FakeBankworks(fail_callproc=True)
    with pytest.raises(DatabaseError, match="complete task 42"):
        TaskRepository(bw, settings).complete(TASK, TaskResult(Path("f"), 1, 1))
    assert bw.log == ["commit", "rollback"]


def test_run_query_sets_array_size(settings):
    bw = FakeBankworks()
    cursor = TaskRepository(bw, settings).run_query("select 1")
    assert cursor.arraysize == 100 and cursor.executed == ["select 1"]


def test_run_query_wraps_database_errors(settings):
    # Regression: the original used a bare except and hid the ORA- message.
    with pytest.raises(DatabaseError, match="ORA-00942"):
        TaskRepository(FakeBankworks(fail=True), settings).run_query("select 1")
