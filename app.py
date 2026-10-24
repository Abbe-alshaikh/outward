import os
import sys
from pathlib import Path

from db import complete_task, get_pending_tasks, get_task, set_container_startup
from okq8common.bankworks.db import Bankworks
from okq8common.utils.config import Config
from okq8common.utils.logger import OKQ8Logging
from pandas import DataFrame, Series
from util import verify_mounts, write_task_to_disk


def handle_task(task: Series):
    """
    Handles a task by getting the task output, writing it to disk, completing the task, and moving the file.

    Args:
        task (Series): A pandas Series containing information about the task.

    Returns:
        None

    """    
    logger.info(
        f"Started {task['INTEGRATION_NAME']} with process_id {task['PROCESS_ID']}"
    )
    task_output = get_task(
        bw3=bw3,
        config=config,
        institution_number=task["INSTITUTION_NUMBER"],
        task_procedure=task["INTEGRATION_NAME"],
        process_id=task["PROCESS_ID"],
    )
    if isinstance(task_output, DataFrame):
        (
            file_name,
            file_count,
            file_size,
            completion_status,
            additional_data,
        ) = write_task_to_disk(bw3=bw3, config=config, task=task_output.iloc[0])
    else:
        logger.error(f"Failed to get task output for {task['PROCESS_ID']} - Exiting")
        sys.exit(os.EX_IOERR)

    completion_parameters = (
        task["PROCESS_ID"],
        file_count,
        file_count,
        completion_status,
        task["INTEGRATION_NAME"],
        "BDH_OUTWARD",
        file_name.name,
        file_size,
        additional_data,
        "",
        "",
    )

    complete_task(bw3=bw3, config=config, parameters=completion_parameters)



    logger.info(f"File name: {file_name}")
    logger.info(f"File count: {file_count}")
    logger.info(f"File size: {file_size}")
    logger.info(f"Completion status: {completion_status}")
    logger.info(f"Additional_data: {additional_data}")
    logger.info(f"Process_id: {task['PROCESS_ID']} completed successfully")


def main():
    """
    The main function of the application. Verifies mounts, sets container startup, gets pending tasks, and handles each task.

    Returns:
        None

    """
        
    # Check file on mount to be sure mount is correct
    if not verify_mounts(config):
        logger.error("Mount checks failed - Exiting")
        sys.exit(os.EX_OSFILE)

    # Let the database services know that we are starting up.
    # This will release any 'locked' records for processing another time
    # The retry_limit should be set to a meaningful value

    if not set_container_startup(bw3=bw3, config=config):
        logger.error("Failed to set container startup - Exiting")
        sys.exit(os.EX_IOERR)

    tasks = get_pending_tasks(bw3=bw3, config=config)

    if not isinstance(tasks, DataFrame):
        logger.error("Failed to get pending tasks - Exiting")
        sys.exit(os.EX_IOERR)

    logger.info(f"{len(tasks)} outward tasks are pending")
    for _, task in tasks.iterrows():
        handle_task(task=task)

    logger.info(f"Finish processing all pending tasks")


if __name__ == "__main__":
    params = Config(Path(__file__).resolve().parent / "config.yaml")
    config = params.cfg
    okq8_logging = OKQ8Logging(config["app"]["log_level"])
    bw3 = Bankworks(config)

    logger = okq8_logging.getlogger("okq8")

    main()

    sys.exit()