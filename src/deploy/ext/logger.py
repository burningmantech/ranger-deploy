"""
Extensions to :mod:`twisted.logger`
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO, cast

from twisted.logger import (
    FilteringLogObserver,
    ILogObserver,
    LogLevel,
    LogLevelFilterPredicate,
    globalLogBeginner,
    globalLogPublisher,
    textFileLogObserver,
)


__all__ = (
    "globalLogLevelPredicate",
    "startLogging",
)


globalLogLevelPredicate = LogLevelFilterPredicate(defaultLogLevel=LogLevel.info)


def startLogging(file: TextIO = sys.stdout) -> None:
    """
    Start Twisted logging system.
    """
    fileObserver = textFileLogObserver(file)
    filteringObserver = FilteringLogObserver(
        fileObserver, (globalLogLevelPredicate,)
    )

    globalLogBeginner.beginLoggingTo(
        [filteringObserver],
        redirectStandardIO=False,
    )


@contextmanager
def logCapture() -> Iterator[list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    observer = cast(ILogObserver, events.append)

    globalLogPublisher.addObserver(observer)

    yield events

    globalLogPublisher.removeObserver(observer)
