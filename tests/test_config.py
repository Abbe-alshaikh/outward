from pathlib import Path

import pytest

from outward.config import Settings, qualified_procedure
from outward.errors import ConfigError
from tests.fakes import settings_dict


def test_parses_environment_strings_into_typed_values(tmp_path):
    s = Settings.from_dict(settings_dict(tmp_path))
    assert (s.chunks, s.array_size, s.retry_limit) == (2, 100, 3)
    assert s.mount_checks == (tmp_path / "mount.check",)
    assert (
        s.procedures.qualified(s.procedures.complete_task)
        == "bw3.bw_okq8_biztalk_integration.update_biztalk_log_ext"
    )
    assert s.successor_timeout is None
    assert s.clob_encoding == "cp1252"


def test_mount_checks_are_split_and_trimmed(tmp_path):
    cfg = settings_dict(tmp_path)
    cfg["app"]["paths"]["mount_checks"] = " /a/x , /b/y ,"
    assert Settings.from_dict(cfg).mount_checks == (Path("/a/x"), Path("/b/y"))


@pytest.mark.parametrize("value", [None, "", " , "])
def test_unset_mount_checks_is_a_config_error(tmp_path, value):
    # Regression: the original crashed with AttributeError on None.split(",").
    cfg = settings_dict(tmp_path)
    cfg["app"]["paths"]["mount_checks"] = value
    with pytest.raises(ConfigError, match="mount_checks"):
        Settings.from_dict(cfg)


@pytest.mark.parametrize("key,value", [("chunks", "abc"), ("chunks", "0"), ("array_size", "-1")])
def test_invalid_numbers_fail_fast(tmp_path, key, value):
    cfg = settings_dict(tmp_path)
    cfg["bankworks"][key] = value
    with pytest.raises(ConfigError, match=key):
        Settings.from_dict(cfg)


def test_missing_key_is_reported(tmp_path):
    cfg = settings_dict(tmp_path)
    del cfg["bankworks"]["institution_number"]
    with pytest.raises(ConfigError, match="institution_number"):
        Settings.from_dict(cfg)


def test_successor_timeout(tmp_path):
    assert Settings.from_dict(settings_dict(tmp_path, successor_timeout="30")).successor_timeout == 30
    assert Settings.from_dict(settings_dict(tmp_path, successor_timeout="0")).successor_timeout is None


@pytest.mark.parametrize(
    "name",
    ["x; execute immediate 'drop table t'", "a b", "1abc", "", "proc(1)", "--"],
)
def test_procedure_names_that_are_not_identifiers_are_rejected(name):
    with pytest.raises(ValueError):
        qualified_procedure("bw3.pkg", name)


def test_invalid_procedure_in_config_is_a_config_error(tmp_path):
    cfg = settings_dict(tmp_path)
    cfg["bankworks"]["procs"]["complete_task"] = "x;drop"
    with pytest.raises(ConfigError, match="Invalid Oracle identifier"):
        Settings.from_dict(cfg)


def test_valid_procedure_name():
    assert qualified_procedure("bw3.pkg", "OUT_TRAPETS$1") == "bw3.pkg.OUT_TRAPETS$1"
