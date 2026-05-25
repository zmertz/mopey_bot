"""
Centralized logging setup for Mopey.

All modules get a logger via:
    from ..utils.log import get_logger
    log = get_logger(__name__)

This keeps log output consistent (same format, same level) across the whole
bot, and everything goes to stdout so your server can capture it normally.

Log levels used:
    DEBUG   — verbose internal state (queue ops, flag changes, etc.)
    INFO    — normal operational events (song started, user connected, etc.)
    WARNING — recoverable problems (Plex unavailable, song skipped due to error)
    ERROR   — unexpected failures with full tracebacks
"""

import logging
import sys


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Call once at startup (in bot.py) to configure the root logger.
    All child loggers (mopey.*) inherit this config.
    """
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger("mopey")
    root.setLevel(level)
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger namespaced under 'mopey'.
    Pass __name__ and the module path becomes the logger name automatically.
    e.g. 'mopey.core.player', 'mopey.cogs.music'
    """
    # Strip leading package name if called with full path like 'mopey.core.player'
    # so loggers are always rooted at 'mopey'
    if not name.startswith("mopey"):
        name = f"mopey.{name}"
    return logging.getLogger(name)