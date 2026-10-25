import sys

import pytest

from outward.config import Settings
from outward.errors import SuccessorError
from outward.models import STATUS_FAILED, STATUS_OK
from outward.successors import (
    BinarySuccessor,
    CsvSuccessor,
    ManifestRow,
    ShellSuccessor,
    build_successors,
)
from tests.fakes import FakeCursor, FakeLob, settings_dict


class FakeQueries:
    """Returns pre-programmed cursors in order and records the SQL it ran."""

    def __init__(self, *cursors):
        self.cursors = list(cursors)
        self.sql = []

    def run_query(self, sql):
        self.sql.append(sql)
        return self.cursors.pop(0)


@pytest.fixture
def settings(tmp_path):
    return Settings.from_dict(settings_dict(tmp_path))


def manifest(tmp_path, *rows):
    path = tmp_path / "manifest.txt"
    path.write_text("\n".join(rows) + "\n", encoding="cp1252")
    return path


# --- ManifestRow ----------------------------------------------------------


def test_parse_full_row(tmp_path):
    row = ManifestRow.parse(f"SQL§select 1 from dual§x§{tmp_path}§out.csv§3§extra\n")
    assert row == ManifestRow("select 1 from dual", tmp_path / "out.csv", "003", "extra")


@pytest.mark.parametrize("kind", ["TABLE", "view", " Table "])
def test_table_and_view_become_select_star(kind):
    assert ManifestRow.parse(f"{kind}§bw3.t§x§/d§f").sql == "select * from bw3.t"


def test_status_and_additional_data_default(tmp_path):
    row = ManifestRow.parse("SQL§select 1§x§/d§f")
    assert (row.completion_status, row.additional_data) == (STATUS_OK, "")


def test_empty_status_column_defaults_to_ok_instead_of_000():
    # Regression: str("").zfill(3) produced "000".
    assert ManifestRow.parse("SQL§select 1§x§/d§f§§").completion_status == STATUS_OK


def test_malformed_row_raises_clear_error():
    # Regression: the original raised a bare IndexError.
    with pytest.raises(SuccessorError, match="expected at least 5"):
        ManifestRow.parse("SQL§select 1§x")


# --- Manifest successors --------------------------------------------------


def test_csv_successor_writes_tab_separated_files(tmp_path, settings):
    out = tmp_path / "out"
    control = manifest(tmp_path, f"TABLE§t1§x§{out}§a.csv", f"SQL§select 2§x§{out}§b.csv§3§info")
    queries = FakeQueries(
        FakeCursor(["A", "B"], [(1, "x"), (2, "y"), (3, "z")]),
        FakeCursor(["C"], []),
    )

    result = CsvSuccessor(queries, settings).run(control)

    assert queries.sql == ["select * from t1", "select 2"]
    assert (out / "a.csv").read_text() == "A\tB\n1\tx\n2\ty\n3\tz\n"
    assert (out / "b.csv").read_text() == "C\n"
    assert not (out / "tmp" / "a.csv").exists()
    assert result.file_count == 2
    assert result.file_size == (out / "a.csv").stat().st_size + (out / "b.csv").stat().st_size
    assert (result.completion_status, result.additional_data) == ("003", "info")  # last row wins


def test_empty_manifest_is_ok_with_zero_files(tmp_path, settings):
    # Regression: the original raised UnboundLocalError on completion_status.
    control = tmp_path / "empty.txt"
    control.write_text("")
    result = CsvSuccessor(FakeQueries(), settings).run(control)
    assert (result.file_count, result.completion_status) == (0, STATUS_OK)


def test_blank_lines_in_manifest_are_ignored(tmp_path, settings):
    control = manifest(tmp_path, "", f"SQL§select 1§x§{tmp_path}§a.csv", "   ")
    result = CsvSuccessor(FakeQueries(FakeCursor(["A"], [])), settings).run(control)
    assert result.file_count == 1


def test_malformed_manifest_fails_before_any_query_runs(tmp_path, settings):
    control = manifest(tmp_path, f"SQL§select 1§x§{tmp_path}§a.csv", "broken")
    queries = FakeQueries(FakeCursor(["A"], []))
    with pytest.raises(SuccessorError):
        CsvSuccessor(queries, settings).run(control)
    assert queries.sql == []
    assert not (tmp_path / "a.csv").exists()


def test_failed_write_does_not_publish_partial_file(tmp_path, settings):
    class ExplodingCursor(FakeCursor):
        def fetchmany(self, size=None):
            raise RuntimeError("connection lost")

    control = manifest(tmp_path, f"SQL§select 1§x§{tmp_path}§a.csv")
    with pytest.raises(RuntimeError):
        CsvSuccessor(FakeQueries(ExplodingCursor(["A"])), settings).run(control)
    assert not (tmp_path / "a.csv").exists()
    assert not (tmp_path / "tmp" / "a.csv").exists()


def test_binary_successor_writes_first_lob(tmp_path, settings):
    control = manifest(tmp_path, f"SQL§select blob§x§{tmp_path}§a.bin")
    result = BinarySuccessor(FakeQueries(FakeCursor(["B"], [(FakeLob(b"\x00\x01payload"),)])), settings).run(control)
    assert (tmp_path / "a.bin").read_bytes() == b"\x00\x01payload"
    assert (result.file_count, result.file_size) == (1, 9)


def test_binary_successor_with_no_rows_raises(tmp_path, settings):
    # Regression: the original did fetchone()[0] on None -> TypeError.
    control = manifest(tmp_path, f"SQL§select blob§x§{tmp_path}§a.bin")
    with pytest.raises(SuccessorError, match="no rows"):
        BinarySuccessor(FakeQueries(FakeCursor(["B"], [])), settings).run(control)


# --- Shell successor (uses the Python interpreter so it runs on any OS) ---


def script(tmp_path, body):
    path = tmp_path / "script.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_shell_success(tmp_path):
    result = ShellSuccessor(interpreter=[sys.executable]).run(script(tmp_path, "print('hi')"))
    assert result.completion_status == STATUS_OK and result.file_count == 0


@pytest.mark.parametrize(
    "body",
    ["import sys; sys.exit(3)", "import sys; sys.stderr.write('warning')"],
)
def test_shell_failure_on_exit_code_or_stderr(tmp_path, body):
    assert ShellSuccessor(interpreter=[sys.executable]).run(script(tmp_path, body)).completion_status == STATUS_FAILED


def test_shell_non_utf8_output_does_not_turn_success_into_failure(tmp_path):
    # Regression: stdout.decode('utf-8') raised and the broad except reported 003.
    body = r"import sys; sys.stdout.buffer.write(b'\xe5\xe4\xf6')"  # 'åäö' in cp1252
    assert ShellSuccessor(interpreter=[sys.executable]).run(script(tmp_path, body)).completion_status == STATUS_OK


def test_shell_timeout_is_a_failure(tmp_path):
    body = "import time; time.sleep(5)"
    result = ShellSuccessor(timeout=0.5, interpreter=[sys.executable]).run(script(tmp_path, body))
    assert result.completion_status == STATUS_FAILED


def test_shell_missing_interpreter_is_a_failure(tmp_path):
    result = ShellSuccessor(interpreter=["definitely-not-a-shell"]).run(tmp_path / "x.sh")
    assert result.completion_status == STATUS_FAILED


def test_registry_contains_all_known_types(settings):
    assert set(build_successors(FakeQueries(), settings)) == {"hshl", "hcsv", "hbin"}
