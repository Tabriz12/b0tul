import logging
import os
import pathlib

CURR_DIR = pathlib.Path(__file__).parent.parent.resolve()


def setup_logger(logger_name: str) -> logging.Logger:
    logger = logging.getLogger(logger_name)

    logpath = os.path.join(CURR_DIR, "logs")
    os.makedirs(logpath, exist_ok=True)

    file_handler = logging.FileHandler(
        os.path.join(logpath, f"{logger_name}.log"), encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    return logger
