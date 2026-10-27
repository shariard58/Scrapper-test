import os
import sys
import logging

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    
    # log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = os.getenv("LOG_LEVEL", "WARNING").upper()
    if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        raise ValueError(f"Invalid log level: {log_level}")
    
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Create a console handler and set its level to the log level
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
