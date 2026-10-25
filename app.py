"""Entry point and composition root.

This is the only module that touches okq8common and the only place that
creates concrete objects. Everything below receives its dependencies through
its constructor, which is what makes it testable without Oracle.
"""

import sys
from pathlib import Path

from okq8common.bankworks.db import Bankworks
from okq8common.utils.config import Config
from okq8common.utils.logger import OKQ8Logging

from outward.config import Settings
from outward.processor import TaskProcessor
from outward.repository import TaskRepository
from outward.service import exit_code, run_batch
from outward.successors import build_successors


def main() -> int:
    config = Config(Path(__file__).resolve().parent / "config.yaml").cfg
    OKQ8Logging(config["app"]["log_level"]).getlogger("okq8")

    def run() -> None:
        settings = Settings.from_dict(config)  # validate before connecting
        repository = TaskRepository(Bankworks(config), settings)
        processor = TaskProcessor(repository, settings, build_successors(repository, settings))
        run_batch(repository, processor, settings)

    return exit_code(run)


if __name__ == "__main__":
    sys.exit(main())
