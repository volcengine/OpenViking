"""
Centralized logging configuration
Set OV_DEBUG=1 environment variable to enable debug logging
"""

import logging
import logging.config
import os
import warnings

# Suppress warnings
warnings.filterwarnings("ignore")

# Check debug mode from environment
DEBUG = os.environ.get("OV_DEBUG") == "1"

if DEBUG:
    # Debug mode - show all logs
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
else:
    # Production mode - aggressively suppress all logs
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": True,
            "formatters": {"null": {"format": ""}},
            "handlers": {
                "null": {
                    "class": "logging.NullHandler",
                },
            },
            "root": {"level": "CRITICAL", "handlers": ["null"]},
            "loggers": {
                # Suppress all OpenViking loggers
                "openviking": {"level": "CRITICAL", "handlers": ["null"], "propagate": False},
                "openviking.agfs_manager": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.viking_vector_index_backend": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.queuefs": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.queuefs.queue_manager": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.vikingdb_manager": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.viking_fs": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.session.session": {
                    "level": "ERROR",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.session.memory_extractor": {
                    "level": "ERROR",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.session.compressor": {
                    "level": "ERROR",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.async_client": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.parse": {"level": "CRITICAL", "handlers": ["null"], "propagate": False},
                "openviking.parse.parsers": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.parse.parsers.markdown": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.storage.queuefs.semantic_processor": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "apscheduler": {"level": "CRITICAL", "handlers": ["null"], "propagate": False},
                "openviking.parse.tree_builder": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
                "openviking.service.core": {
                    "level": "CRITICAL",
                    "handlers": ["null"],
                    "propagate": False,
                },
            },
        }
    )

    # Additional enforcement: set all loggers after config
    for logger_name in ["openviking", "apscheduler"]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False
