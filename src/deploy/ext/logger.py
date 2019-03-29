"""
Extensions to :mod:`twisted.logger`
"""

import sys
from typing import TextIO

from twisted.logger import globalLogBeginner, textFileLogObserver


__all__ = (
    "startLogging",
)


def startLogging(file: TextIO = sys.stdout) -> None:
    """
    Start Twisted logging system.
    """
    globalLogBeginner.beginLoggingTo(
        [textFileLogObserver(file)], redirectStandardIO=False,
    )
