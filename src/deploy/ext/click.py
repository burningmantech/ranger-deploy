"""
Extensions to :mod:`click`
"""

import sys
from configparser import ConfigParser, ExtendedInterpolation
from enum import Enum, auto
from io import StringIO
from pathlib import Path
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)
from unittest.mock import patch

import click
from attr import Factory, attrs
from click import option


__all__ = (
    "ClickTestResult",
    "clickTestRun",
    "composedOptions",
    "profileOption",
    "readConfig",
    "trialRunOption",
)


defaultConfigPath = Path("~/.ranger-deploy.ini")
defaultConfigProfile = "default"


def composedOptions(
    *options: Callable[..., Callable]
) -> Callable[..., Callable]:
    """
    Combines options decorators into a single decorator.
    """

    def wrapper(f: Callable) -> Callable:
        for o in reversed(options):
            f = o(f)
        return f

    return wrapper


profileOption = option(
    "--profile",
    help="Profile to load from configuration file",
    type=str,
    metavar="<name>",
    prompt=False,
    required=False,
)

trialRunOption = option(
    "--trial-run", help="Trial run only (do not deploy)", is_flag=True
)


class Internal(Enum):
    UNSET = auto()


@attrs(auto_attribs=True, slots=True, kw_only=True)
class ClickTestResult:
    """
    Captured results after testing a click command.
    """

    echoOutputType: ClassVar = List[Tuple[str, Mapping[str, Any]]]

    exitCode: Union[int, None, Internal] = Internal.UNSET

    echoOutput: echoOutputType = Factory(list)

    stdin: StringIO = Factory(StringIO)
    stdout: StringIO = Factory(StringIO)
    stderr: StringIO = Factory(StringIO)

    beginLoggingToCalls: Sequence[Any] = ()


def clickTestRun(
    main: Callable[[], None], arguments: List[str]
) -> ClickTestResult:
    """
    Context manager for testing click applications.
    """
    assert len(arguments) > 0

    result = ClickTestResult()

    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    sys.stdin = result.stdin
    sys.stdout = result.stdout
    sys.stderr = result.stderr

    argv = sys.argv
    sys.argv = arguments

    def captureExit(code: Optional[int] = None) -> None:
        # assert result.exitCode == Internal.UNSET, "repeated call to exit()"
        result.exitCode = code

    exit = sys.exit
    sys.exit = cast(Callable, captureExit)

    def captureEcho(format: str, **kwargs: Any) -> None:
        result.echoOutput.append((format, kwargs))

    echo = click.echo
    click.echo = cast(Callable, captureEcho)

    with patch(
        "twisted.logger.globalLogBeginner.beginLoggingTo"
    ) as beginLoggingTo:
        main()

    result.beginLoggingToCalls = beginLoggingTo.call_args_list

    sys.stdin = stdin
    sys.stdout = stdout
    sys.stderr = stderr
    sys.argv = argv
    sys.exit = exit
    click.echo = echo

    return result


def readConfig(
    profile: Optional[str] = defaultConfigProfile,
    path: Path = defaultConfigPath,
) -> Dict[str, Optional[str]]:
    """
    Read configuration from the given path using the given profile.
    """
    if profile is None:
        profile = defaultConfigProfile

    path = path.expanduser()

    parser = ConfigParser(
        delimiters=("=",),
        comment_prefixes=("#",),
        interpolation=ExtendedInterpolation(),
        strict=True,
        default_section="default",
    )
    parser.read(path)

    try:
        section = parser[profile]
    except KeyError:
        return {}

    return dict(section.items())
