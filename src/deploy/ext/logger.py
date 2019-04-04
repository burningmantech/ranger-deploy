"""
Extensions to :mod:`twisted.logger`
"""

import sys
from typing import TextIO

from twisted.logger import (
    FilteringLogObserver, LogLevelFilterPredicate,
    globalLogBeginner, textFileLogObserver,
)


__all__ = (
    "globalLogLevelPredicate",
    "startLogging",
)


globalLogLevelPredicate = LogLevelFilterPredicate()


def startLogging(file: TextIO = sys.stdout) -> None:
    """
    Start Twisted logging system.
    """
    fileObserver = textFileLogObserver(file)
    filteringObserver = FilteringLogObserver(
        fileObserver, (globalLogLevelPredicate,)
    )

    globalLogBeginner.beginLoggingTo(
        [filteringObserver], redirectStandardIO=False,
    )
