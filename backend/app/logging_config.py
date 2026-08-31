"""Logging setup — the backend's only trace of what it did.

Until this existed the service was silent: an indexing run that failed at 3am
left nothing behind but a row in the wrong state.  So every stage of the data
embedding pipeline logs, and the log survives the process that wrote it.

Two handlers, deliberately:
  * the console, because that is where a developer is already looking;
  * a rotating file, because the console scrolls away and a run that went wrong
    is usually noticed later.

Called once from app.main at import time.  Modules take their own logger with
``logging.getLogger(__name__)`` so a line names the service that emitted it.

One caveat worth knowing: the rotating file handler is not safe across
processes.  Two servers started from this directory write to the same file and
will interleave — and can lose lines if they rotate at the same moment.  That is
only reachable by running a second server by hand, so it is documented rather
than locked against.
"""

import logging
import logging.config
from typing import Any

from app.config import settings

# Console stays readable; the file carries the detail worth grepping later.
CONSOLE_FORMAT = "%(levelname)-8s %(name)s | %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"

# One log file per few million lines is plenty to look back through, and five
# of them bounds the disk a long-running dev session can consume.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Third-party loggers that are noisy at INFO and say nothing we need. The
# vendor SDKs narrate every request, which buries our own lines — and the
# pipeline already logs the calls that matter, with the file they were for.
QUIET_LOGGERS = (
    "botocore",
    "boto3",
    "urllib3",
    "s3transfer",
    "openai",
    "httpx",
    # The OpenAI SDK vendors its own httpx under this name.
    "httpx2",
    "httpcore",
    "anthropic",
    "pinecone",
    "pinecone_plugin_interface",
)


def _config() -> dict[str, Any]:
    """Build the dictConfig for the application's loggers."""
    return {
        "version": 1,
        # Uvicorn installs its own loggers before this runs; leaving them alone
        # keeps the access log working rather than silencing the server.
        "disable_existing_loggers": False,
        "formatters": {
            "console": {"format": CONSOLE_FORMAT},
            "file": {"format": FILE_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console",
                "level": "INFO",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "file",
                "level": "DEBUG",
                "filename": str(settings.log_path),
                "maxBytes": LOG_MAX_BYTES,
                "backupCount": LOG_BACKUP_COUNT,
                "encoding": "utf-8",
            },
        },
        # Our own package logs at DEBUG to the file, INFO to the console.
        "loggers": {
            "app": {"handlers": ["console", "file"], "level": "DEBUG", "propagate": False},
            **{
                name: {"handlers": ["console", "file"], "level": "WARNING", "propagate": False}
                for name in QUIET_LOGGERS
            },
        },
        "root": {"handlers": ["console", "file"], "level": "INFO"},
    }


def configure() -> None:
    """Install the application's logging configuration.

    Creates the log directory if it is missing, since a file handler raises
    rather than creating a parent directory itself.
    """
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(_config())
