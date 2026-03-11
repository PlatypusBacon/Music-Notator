"""
bedug.py
─────────────────
Centralised logging configuration for backend.

Produces colour-coded, timestamped output to stdout and optionally
to a rotating log file. Import and call setup_logging() once at
startup (main.py and celery worker entry point both call it).

Log levels by component
───────────────────────
  uvicorn / FastAPI   INFO   — request/response lines
  workers.tasks       DEBUG  — every pipeline milestone
  pipeline.*          DEBUG  — per-function entry/exit + timing
  celery              INFO   — task received / succeeded / failed
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


# ANSI colour codes for terminal output
_RESET  = "\033[0m"
_GREY   = "\033[38;5;240m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_BOLD   = "\033[1m"

LEVEL_COLOURS = {
    "DEBUG":    _GREY,
    "INFO":     _CYAN,
    "WARNING":  _YELLOW,
    "ERROR":    _RED,
    "CRITICAL": _BOLD + _RED,
}


class ColourFormatter(logging.Formatter):
    """Colour-coded formatter for terminal output."""

    FMT = "{colour}[{levelname:8s}]{reset}  {grey}{asctime}{reset}  {bold}{name}{reset}  {message}"

    def format(self, record: logging.LogRecord) -> str:
        colour = LEVEL_COLOURS.get(record.levelname, "")
        fmt = self.FMT.format(
            colour=colour,
            levelname=record.levelname,
            reset=_RESET,
            grey=_GREY,
            asctime="%(asctime)s",
            bold=_BOLD,
            name="%(name)s",
            message="%(message)s",
        )
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S", style="%")
        return formatter.format(record)


def setup_logging(
    level: str = "DEBUG",
    log_file: str | Path | None = None,
) -> None:
    """
    Configure root logger. Call once at application startup.

    Parameters
    ──────────
    level     Root log level string: DEBUG | INFO | WARNING | ERROR
    log_file  Optional path for a rotating file handler (in addition to stdout).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    # Remove any existing handlers (uvicorn sometimes installs its own)
    root.handlers.clear()

    # ── Stdout handler (colour) ────────────────────────────────────────────
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(ColourFormatter())
    root.addHandler(stdout_handler)

    # ── Optional rotating file handler (plain text) ────────────────────────
    if log_file:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,   # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                fmt="[%(levelname)-8s] %(asctime)s  %(name)s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)

    # ── Quieten noisy third-party loggers ──────────────────────────────────
    for noisy in ("urllib3", "httpx", "httpcore", "multipart", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised  level=%s  file=%s", level, log_file or "stdout only"
    )