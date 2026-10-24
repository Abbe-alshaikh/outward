import csv
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
import shutil

import oracledb
from okq8common.bankworks.db import Bankworks
from pandas import Series

logger = logging.getLogger("okq8")


def verify_mounts(config: dict) -> bool:
    """
    Verifies that all mount files specified in the configuration file exist.

    Args:
        config (dict): A dictionary containing configuration information.

    Returns:
        bool: True if all mount files exist, False otherwise.

    """
    all_mounts_valid = True
    for file in config["app"]["paths"]["mount_checks"].split(","):
        if not Path(file).is_file():
            logger.error(f"Mount file {file} does not exist")
            all_mounts_valid = False

    return all_mounts_valid


def write_task_to_disk(bw3: Bankworks, config: dict, task: Series) -> tuple:
    """Save the tasks blobs to disk and call any relevant successors

    This function appends a .temp suffix to the file name to ensure that it is not picked by
    Biztalk until we are certain that the process has completed in the database.

    Args:
        bw3 (Bankworks): The bankworks database connection
        config (dict): The configuration dictionary
        task (Series): The task row returned by the database

    Returns:
        tuple:
            file_name
            file_count
            file_size
            completion_status
            additional_data
    """
    
    db_file_name = Path(task["OUTWARD_FILENAME"])
    
    # Make sure the tmp folder exists
    tmp_folder = db_file_name.parent / "tmp"
    tmp_folder.mkdir(parents=True, exist_ok=True)
    
    # Ensure that the file is writtent to the tmp folder
    file_name = tmp_folder / db_file_name.name
    
    chunks = config["bankworks"]["chunks"]
    write_lob(file_name=file_name, lob=task["OUTWARD_DATA"], chunks=chunks)

    file_count = 1
    file_size = file_name.stat().st_size
    completion_status = "002"
    additional_data = ""

    # The file is written to tmp sub folder, we now move it to the correct folder
    move_file(file_name)

    # Check if the archive file name contains a path
    if task["ARCHIVE_FILENAME"] and len(task["ARCHIVE_FILENAME"]) > 0:
        if Path(task["ARCHIVE_FILENAME"]).parent == Path('.'):
            archive_file = (
                Path(config["app"]["paths"]["archive"])
                / date.today().strftime("%Y/%m/%d")
                / task["ARCHIVE_FILENAME"]
            )
        else:
            archive_file = Path(task["ARCHIVE_FILENAME"])

        write_lob(file_name=archive_file, lob=task["ARCHIVE_DATA"], chunks=chunks)

    # Check if successor processing is required.
    if task["OUTWARD_SUCCESSOR"]:
        fc, fs, completion_status, additional_data = call_successors(
            bw3=bw3,
            config=config,
            successor_type=task["OUTWARD_SUCCESSOR"].lower(),
            successor_name=task["OUTWARD_FILENAME"],
        )
        if fc != 0:
            file_count += fc
            file_size += fs

    return (
        file_name,
        file_count,
        file_size,
        completion_status,
        additional_data,
    )


def process_successor_row(line: str) -> tuple:
    """
    Processes a row from a successor file and returns a tuple containing information about the successor.

    Args:
        line (str): A string containing a row from a successor file.

    Returns:
        tuple: A tuple containing the completion status, additional data, SQL statement, and file name for the successor.

    """
    columns = line.strip().split("§")

    # Get the filename
    successor_file_name = Path(columns[3]) / columns[4]

    # Make sure the directory exists
    successor_file_name.parent.mkdir(parents=True, exist_ok=True)

    # Set the completion status
    try:
        completion_status = str(columns[5]).zfill(3)
    except IndexError:
        completion_status = "002"

    # Get the additional data
    try:
        additional_data = columns[6]
    except IndexError:
        additional_data = ""

    # Format the SQL statement
    if columns[0].upper() == "TABLE" or columns[0].upper() == "VIEW":
        sql = "select * from " + columns[1]
    else:
        sql = columns[1]

    return completion_status, additional_data, sql, successor_file_name


def call_successors(
    bw3: Bankworks, config: dict, successor_type: str, successor_name: str
) -> tuple:
    """
    Calls a successor job and returns information about the files generated.

    Args:
        bw3 (Bankworks): The Bankworks object to use to execute the successor job.
        config (dict): A dictionary containing configuration information.
        successor_type (str): The type of file to be generated ("hshl", "hcsv", or "hbin").
        successor_name (str): The name of the successor job to be executed.

    Returns:
        Tuple[int, int, str, str]: A tuple containing the number of files generated, the total size of the files,
        the completion status of the job, and any additional data.

    """
    if successor_type == "hshl":
        file_count, file_size, completion_status, additional_data = run_bash(
            successor_name=successor_name
        )
    elif successor_type == "hcsv" or successor_type == "hbin":
        file_count, file_size, completion_status, additional_data = handle_file(
            bw3=bw3, config=config, file_name=Path(successor_name), type=successor_type
        )
    else:
        file_count = 0
        file_size = 0
        completion_status = "002"
        additional_data = ""

    return file_count, file_size, completion_status, additional_data


def handle_file(bw3: Bankworks, config: dict, file_name: Path, type: str) -> tuple:
    file_count = 0
    file_size = 0
    with open(file_name, "r", encoding="cp1252") as file:
        for line in file:
            (
                completion_status,
                additional_data,
                sql,
                successor_file_name,
            ) = process_successor_row(line)
            file_count += 1
            file_size += write_successor_data(
                bw3=bw3,
                sql=sql,
                config=config,
                file_name=Path(successor_file_name),
                type=type,
            )

    return file_count, file_size, completion_status, additional_data


def write_successor_data(
    bw3: Bankworks, sql: str, config: dict, file_name: Path, type: str
) -> int:
    """
    Executes a SQL statement and writes the results to a file.

    Args:
        bw3 (Bankworks): The Bankworks object to use to execute the SQL statement.
        sql (str): The SQL statement to execute.
        config (dict): A dictionary containing configuration information.
        file_name (Path): The name of the file to write the results to.
        type (str): The type of file to write ("hcsv" or "hbin").

    Returns:
        int: The number of bytes written to the file.

    """
    logger.info(f"Started file {file_name}")

    # Make sure the directory exists
    file_name.parent.mkdir(parents=True, exist_ok=True)

    # Set the fetch size
    bw3.curBw3.arraysize = int(config["bankworks"]["array_size"])

    # Try to execute the SQL statement
    try:
        bw3.curBw3.execute(sql)
    except:
        logger.error(f"Error processing file {file_name}")
        sys.exit(os.EX_IOERR)

    if type == "hcsv":
        write_csv(file_name=file_name, cursor=bw3.curBw3)
    elif type == "hbin":
        write_lob(
            file_name=file_name,
            lob=bw3.curBw3.fetchone()[0],
            chunks=config["bankworks"]["chunks"],
        )

    logger.info(f"Finished file {file_name}. {file_name.stat().st_size} bytes written")

    return file_name.stat().st_size


def write_csv(file_name: Path, cursor: oracledb.CURSOR) -> None:
    """
    Writes the results of an Oracle cursor to a CSV file.

    Args:
        file_name (str): The name of the file to be saved to disk.
        cursor (oracledb.CURSOR): The cursor to be used to write to the file.

    Returns:
        None

    """
    column_names = []
    for col in cursor.description:
        column_names.append(col[0])
    with open(file_name, "w", newline="") as file:
        csv_writer = csv.writer(file, dialect="excel", delimiter="\t")
        csv_writer.writerow(column_names)
        rows = cursor.fetchmany()
        while rows:
            for row in rows:
                csv_writer.writerow(row)
            rows = cursor.fetchmany()


def write_lob(file_name: Path, lob: oracledb.LOB, chunks: int = 100) -> None:
    """
    Writes a BLOB or CLOB to a file.

    Args:
        file_name (Path): The path to the file to write.
        lob (oracledb.LOB): The BLOB or CLOB to write to the file.
        chunks (int): The number of chunks to read from the BLOB or CLOB at a time. Defaults to 100.

    Returns:
        None

    """
    file_name.parent.mkdir(parents=True, exist_ok=True)

    chunk_size = min(lob.getchunksize() * int(chunks), lob.size())
    lob_size = lob.size()

    logger.info(
        f"Started writing blob of size {lob_size} bytes in {chunk_size} byte chunks into file {file_name}"
    )
    pos = 0

    with open(file_name, "wb") as file:
        while pos < lob_size:
            data = lob.read(pos + 1, chunk_size)
            pos += len(data)
            file.write(data)


def run_bash(successor_name: str) -> tuple:
    """
    Runs a shell script and returns information about the execution.

    Args:
        successor_name (str): The name of the shell script to run.

    Returns:
        tuple: A tuple containing the following elements:
            - file_count (int): The number of files processed by the shell script.
            - file_size (int): The total size of the files processed by the shell script.
            - completion_status (str): A completion status code indicating the success or failure of the shell script.
            - additional_data (str): Additional data returned by the shell script.

    Raises:
        Exception: If the shell script fails to execute.

    """
    file_size = 0
    file_count = 0
    additional_data = ""
    try:
        process = subprocess.Popen(
            ["sh", successor_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()

        logger.info(f"{successor_name} - stdout - {stdout.decode('utf-8')}") if len(
            stdout
        ) > 0 else None
        logger.error(f"{successor_name} - stderr - {stderr.decode('utf-8')}") if len(
            stderr
        ) > 0 else None

        if process.returncode == 0 and len(stderr) == 0:
            completion_status = "002"
            logger.info(
                f"Shell script {successor_name} ran with return code {process.returncode}"
            )
        else:
            completion_status = "003"
            logger.info(
                f"Shell script {successor_name} failed with return code {process.returncode}"
            )
    except Exception as e:
        logger.error(f"Shell script {successor_name} failed with error {e}")
        completion_status = "003"

    return file_count, file_size, completion_status, additional_data


def move_file(source: Path) -> None:
    """
    Moves a file from tmp folder down one level to the destination folder.

    Args:
        source (Path): The name of the source file.

    Returns:
        None

    """

    dest = source.parent.parent / source.name
    logger.info(f"Moving file {source} to {dest}")
    
    try:
        shutil.move(source, dest)
    except Exception as e:
        logger.error(f"Error moving file {source} with error {e}")
        sys.exit(os.EX_IOERR)
