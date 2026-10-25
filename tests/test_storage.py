from datetime import date
from pathlib import Path

import pytest

from outward.errors import MountCheckError, StorageError
from outward.storage import (
    archive_path,
    publish,
    staged_file,
    staging_path,
    verify_mounts,
    write_lob,
)
from tests.fakes import FakeLob


class StalledLob(FakeLob):
    def read(self, offset, amount):
        return b""


def test_verify_mounts_passes_when_all_files_exist(tmp_path):
    (tmp_path / "a").write_text("x")
    verify_mounts([tmp_path / "a"])


def test_verify_mounts_reports_every_missing_file(tmp_path, caplog):
    (tmp_path / "a").write_text("x")
    with pytest.raises(MountCheckError, match="2 mount"):
        verify_mounts([tmp_path / "a", tmp_path / "b", tmp_path / "c"])
    assert "b does not exist" in caplog.text and "c does not exist" in caplog.text


def test_write_blob_in_chunks(tmp_path):
    lob = FakeLob(b"0123456789", chunk_size=2)
    size = write_lob(tmp_path / "sub" / "f.bin", lob, chunks=2)
    assert (tmp_path / "sub" / "f.bin").read_bytes() == b"0123456789"
    assert size == 10
    assert lob.reads == 3  # 4 + 4 + 2 bytes


def test_write_clob_is_encoded(tmp_path):
    # Regression: the original wrote str into a binary file -> TypeError.
    size = write_lob(tmp_path / "f.txt", FakeLob("åäö text"), chunks=1, clob_encoding="cp1252")
    assert (tmp_path / "f.txt").read_bytes() == "åäö text".encode("cp1252")
    assert size == 8


@pytest.mark.parametrize("lob", [None, FakeLob(b"")])
def test_null_or_empty_lob_writes_empty_file(tmp_path, lob):
    # Regression: the original crashed on NULL with AttributeError.
    assert write_lob(tmp_path / "f", lob, chunks=1) == 0
    assert (tmp_path / "f").read_bytes() == b""


def test_lob_that_stops_returning_data_raises_instead_of_looping_forever(tmp_path):
    with pytest.raises(StorageError, match="no data"):
        write_lob(tmp_path / "f", StalledLob(b"abc"), chunks=1)


def test_staging_path_is_tmp_subfolder():
    assert staging_path(Path("/out/x/file.txt")) == Path("/out/x/tmp/file.txt")


def test_publish_moves_and_overwrites(tmp_path):
    staged, final = tmp_path / "tmp" / "f", tmp_path / "f"
    staged.parent.mkdir()
    staged.write_text("new")
    final.write_text("old")
    publish(staged, final)
    assert final.read_text() == "new" and not staged.exists()


def test_publish_failure_is_a_storage_error(tmp_path):
    with pytest.raises(StorageError, match="left in place"):
        publish(tmp_path / "tmp" / "missing", tmp_path / "f")


def test_staged_file_publishes_on_success(tmp_path):
    final = tmp_path / "out.csv"
    with staged_file(final) as staged:
        assert not final.exists()
        staged.write_text("data")
    assert final.read_text() == "data"


def test_staged_file_cleans_up_and_does_not_publish_on_error(tmp_path):
    final = tmp_path / "out.csv"
    with pytest.raises(RuntimeError):
        with staged_file(final) as staged:
            staged.write_text("partial")
            raise RuntimeError("boom")
    assert not final.exists() and not staged.exists()


def test_archive_path_without_directory_goes_into_dated_folder(tmp_path):
    p = archive_path(tmp_path, "a.txt", date(2024, 3, 7))
    assert p == tmp_path / "2024" / "03" / "07" / "a.txt"


def test_archive_path_with_directory_is_used_as_is(tmp_path):
    assert archive_path(tmp_path, "/elsewhere/a.txt", date(2024, 3, 7)) == Path("/elsewhere/a.txt")
