"""Filesystem operations: mount checks, streaming LOBs to disk, atomic publishing.

BizTalk polls the destination folders, so a file must never appear there
half-written. Files are first written to a ``tmp`` sub-folder (staging) and
then moved into place with ``os.replace``, which is atomic within a filesystem.
"""

import logging
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from outward.errors import MountCheckError, StorageError
from outward.models import Lob

logger = logging.getLogger("okq8")

STAGING_DIR = "tmp"


def verify_mounts(check_files: Iterable[Path]) -> None:
    """Raise MountCheckError if any of the marker files is missing."""
    missing = [f for f in check_files if not f.is_file()]
    for f in missing:
        logger.error(f"Mount file {f} does not exist")
    if missing:
        raise MountCheckError(f"{len(missing)} mount check file(s) missing")


def write_lob(path: Path, lob: Lob | None, chunks: int, clob_encoding: str = "cp1252") -> int:
    """Stream a BLOB or CLOB to ``path`` and return the number of bytes written.

    A NULL LOB produces an empty file, the same result as an empty LOB.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if lob is None:
        logger.warning(f"LOB for {path} is NULL - writing an empty file")
        path.write_bytes(b"")
        return 0

    lob_size = lob.size()
    chunk_size = max(1, min(lob.getchunksize() * chunks, lob_size))
    logger.info(
        f"Started writing blob of size {lob_size} in {chunk_size} chunks into file {path}"
    )

    pos = 0
    try:
        with open(path, "wb") as file:
            while pos < lob_size:
                data = lob.read(pos + 1, chunk_size)
                if not data:
                    # Without this guard a short LOB would loop forever.
                    raise StorageError(f"LOB read returned no data at offset {pos + 1} of {lob_size} for {path}")
                pos += len(data)
                # CLOB reads return str; the file is opened in binary mode.
                file.write(data.encode(clob_encoding) if isinstance(data, str) else data)
    except OSError as e:
        raise StorageError(f"Failed writing {path}: {e}") from e

    return path.stat().st_size


def staging_path(final: Path) -> Path:
    """Where a file is written before it is published to ``final``."""
    return final.parent / STAGING_DIR / final.name


def publish(staged: Path, final: Path) -> None:
    """Atomically move a staged file into its final location."""
    logger.info(f"Moving file {staged} to {final}")
    try:
        os.replace(staged, final)
    except OSError as e:
        raise StorageError(
            f"Failed to publish {staged} to {final}: {e}. The staged file has been left in place for recovery"
        ) from e


@contextmanager
def staged_file(final: Path) -> Iterator[Path]:
    """Yield a staging path and publish it to ``final`` when the block succeeds."""
    staged = staging_path(final)
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        yield staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    publish(staged, final)


def archive_path(archive_root: Path, archive_filename: str, today: date) -> Path:
    """Archive files without a directory go under ``archive_root/YYYY/MM/DD``."""
    name = Path(archive_filename)
    if name.parent == Path("."):
        return archive_root / today.strftime("%Y/%m/%d") / name
    return name
