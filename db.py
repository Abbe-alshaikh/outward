import logging

import oracledb
from okq8common.bankworks.db import Bankworks
from pandas import DataFrame

logger = logging.getLogger("okq8")


def set_container_startup(bw3: Bankworks, config: dict) -> bool:
    """Notify the database that the container is restarting.  This will reset the status
    of any tasks that were started by this container and are not flagged as completed.

    Args:
        bw3 (Bankworks): The bankworks database connection
        config (dict): The configuration dictionary

    Returns:
        DataFrame: The sysrefcursor returned from the database call
    """
    db_proc = (
        config["bankworks"]["procs"]["package"]
        + "."
        + config["bankworks"]["procs"]["container_startup"]
    )
    parameters = dict(
        container_id=config["app"]["container_id"],
        retry_limit=int(config["bankworks"]["retry_limit"]),
    )
    return bw3.callproc(procedure=db_proc, parameters=parameters)


def get_pending_tasks(bw3: Bankworks, config: dict) -> DataFrame:
    """Get a task from the database

    Args:
      bw3 (Bankworks): The bankworks database connection
      config (dict): The configuration dictionary

    Returns:
        DataFrame: A sys refcursor returned from the database call
    """
    # Now get pending tasks
    db_proc = (
        config["bankworks"]["procs"]["package"]
        + "."
        + config["bankworks"]["procs"]["get_pending_tasks"]
    )
    parameters = dict(
        p_institution_number=config["bankworks"]["institution_number"],
        data=bw3.curBw3.var(oracledb.CURSOR),
        container_id=config["app"]["container_id"],
    )
    return bw3.callproc_returning_cursor(
        procedure=db_proc, parameters=parameters, cursor_field=1
    )


def get_task(
    bw3: Bankworks,
    config: dict,
    institution_number: str,
    task_procedure: str,
    process_id: str,
) -> DataFrame:
    """Get an generic outward task

    Args:
        bw3 (Bankworks): The bankworks database connection
        config (dict): The configuration dictionary
        institution_number (str): The institution number
        task_procedure (str): The name of the task procedure
        process_id (str): The biztalk log id

    Returns:
        DataFrame: _description_
    """

    db_proc = config["bankworks"]["procs"]["package"] + "." + task_procedure

    parameters = dict(
        institution_number=institution_number,
        process_id=process_id,
        data=bw3.curBw3.var(oracledb.CURSOR),
    )

    return bw3.callproc_returning_cursor(
        procedure=db_proc, parameters=parameters, cursor_field=2
    )


def complete_task(bw3: Bankworks, config: dict, parameters: tuple) -> None:
    """Flag the task as complete in the database and add some performance information

    Args:
        bw3 (Bankworks): The bankworks database connection
        config (dict): The configuration dictionary
        parameters (list): A dictionary of the parameters to pass to the database call
    """

    #
    # Need to commit before calling the completion tasks
    # This is special log for serial processes like Trapets 
    #
    bw3.dbConn.commit()
    
    
    db_proc = (
        config["bankworks"]["procs"]["package"]
        + "."
        + config["bankworks"]["procs"]["complete_task"]
    )
    bw3.curBw3.callproc(db_proc, parameters)

    # Final commit after completion
    bw3.dbConn.commit()