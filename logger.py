#!/usr/bin/env python3
"""Standard logging template in preferred format"""

import logging
from logging.handlers import RotatingFileHandler


# Logging configuration
log = logging.getLogger(__name__)
log.setLevel(getattr(logging, "INFO"))
log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
# Log to file
# Ensures logs are written to the project folder even if the script is
# executed from another directory
log_path = "/".join(__file__.split("/")[:-1])
log_file = RotatingFileHandler(
    filename="{}/application.log".format(log_path if log_path else '.'),
    maxBytes=10 * 1024 * 1024,  # Bytes to Megabytes
    backupCount=5
    )
log_file.setFormatter(log_format)
log.addHandler(log_file)
# Log to console
log_stream = logging.StreamHandler()
log_stream.setFormatter(log_format)
log.addHandler(log_stream)
