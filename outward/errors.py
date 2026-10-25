"""Exception hierarchy for the service.

Library code raises; only the entry point decides to exit. Each error carries
the process exit code the container should report. The values follow BSD
sysexits.h and are spelled out because ``os.EX_*`` only exists on Unix.
"""

EX_SOFTWARE = 70
EX_OSFILE = 72
EX_IOERR = 74
EX_CONFIG = 78


class OutwardError(Exception):
    """Base class for all expected failures of the service."""

    exit_code = EX_SOFTWARE


class ConfigError(OutwardError):
    """The configuration is missing or invalid."""

    exit_code = EX_CONFIG


class MountCheckError(OutwardError):
    """A mount check file is missing, so the share is probably not mounted."""

    exit_code = EX_OSFILE


class DatabaseError(OutwardError):
    """A BankWorks database call failed or returned unexpected data."""

    exit_code = EX_IOERR


class StorageError(OutwardError):
    """Writing or moving a file failed."""

    exit_code = EX_IOERR


class SuccessorError(OutwardError):
    """A successor manifest or successor query failed."""

    exit_code = EX_IOERR
