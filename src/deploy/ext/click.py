"""
Extensions to :mod:`click`
"""

import sys
from enum import Enum, auto
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union, cast

from attr import Factory, attrs

import click


__all__ = (
    "clickTestRun",
    "ClickTestResult",
)



class Internal(Enum):
    UNSET = auto()



@attrs(auto_attribs=True, slots=True, kw_only=True)
class ClickTestResult(object):
    """
    Captured results after testing a click command.
    """

    exitCode: Union[int, None, Internal] = Internal.UNSET
    echoOutput: List[Tuple[str, Mapping]] = Factory(list)



def clickTestRun(
    main: Callable[[], None], arguments: List[str]
) -> ClickTestResult:
    """
    Context manager for testing click applications.
    """
    assert len(arguments) > 0

    result = ClickTestResult()

    def captureExit(code: Optional[int] = None) -> None:
        # assert result.exitCode == Internal.UNSET, "repeated call to exit()"
        result.exitCode = code

    argv = sys.argv
    sys.argv = arguments

    exit = sys.exit
    sys.exit = cast(Callable, captureExit)

    def captureEcho(format: str, **kwargs: Any) -> None:
        result.echoOutput.append((format, kwargs))

    echo = click.echo
    click.echo = cast(Callable, captureEcho)

    main()

    sys.argv = argv
    sys.exit = exit
    click.echo = echo

    return result
