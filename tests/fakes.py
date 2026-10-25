"""Hand-written test doubles. They are simpler and more readable than mocks."""

from pathlib import Path

import oracledb


class FakeLob:
    """Mimics oracledb.LOB: 1-based offsets, returns bytes (BLOB) or str (CLOB)."""

    def __init__(self, data: bytes | str, chunk_size: int = 4):
        self.data = data
        self.chunk_size = chunk_size
        self.reads = 0

    def getchunksize(self) -> int:
        return self.chunk_size

    def size(self) -> int:
        return len(self.data)

    def read(self, offset: int, amount: int):
        self.reads += 1
        return self.data[offset - 1 : offset - 1 + amount]


class FakeCursor:
    """Mimics the parts of an oracledb cursor used for successor queries."""

    def __init__(self, columns=(), rows=(), fail=False):
        self.description = [(c,) for c in columns]
        self.rows = list(rows)
        self.arraysize = 2
        self.fail = fail
        self.executed = []

    def execute(self, sql):
        if self.fail:
            raise oracledb.DatabaseError("ORA-00942: table or view does not exist")
        self.executed.append(sql)
        return self

    def fetchmany(self, size=None):
        size = size or self.arraysize
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def settings_dict(tmp_path: Path, **app_overrides) -> dict:
    mount = tmp_path / "mount.check"
    mount.write_text("ok")
    app = {
        "container_id": "outward_test",
        "log_level": "20",
        "paths": {"mount_checks": str(mount), "archive": str(tmp_path / "archive")},
    }
    app.update(app_overrides)
    return {
        "app": app,
        "bankworks": {
            "array_size": "100",
            "chunks": "2",
            "retry_limit": "3",
            "institution_number": "00000051",
            "procs": {
                "package": "bw3.bw_okq8_biztalk_integration",
                "get_pending_tasks": "get_pending_tasks_v2",
                "complete_task": "update_biztalk_log_ext",
                "container_startup": "container_startup",
            },
        },
    }
