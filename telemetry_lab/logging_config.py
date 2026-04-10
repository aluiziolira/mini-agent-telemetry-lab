"""Structured logging configuration for the telemetry lab."""

import json
import logging
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for attr in ["trace_id", "span_id", "request_id"]:
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)

        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry)


def get_logging_config():
    """Return Django LOGGING configuration dict."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": StructuredFormatter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
            },
        },
        "loggers": {
            "telemetry_lab": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
