"""
Extensions to :mod:`hypothesis`
"""

from os import environ
from string import ascii_letters, digits, printable
from typing import Callable, Optional

from hypothesis import HealthCheck, settings
from hypothesis.strategies import SearchStrategy, composite, integers, text


__all__ = (
    "ascii_text",
    "user_names",
    "host_names",
    "port_numbers",
    "email_addresses",
    "repository_ids",
)


settings.register_profile(
    "CI",
    deadline=None,
    max_examples=settings().max_examples * 10,
    suppress_health_check=(HealthCheck.too_slow,),
)

if environ.get("CI", "False").lower() in ("true", "yes", "1"):
    settings.load_profile("CI")  # pragma: no cover


def ascii_text(
    min_size: Optional[int] = 0, max_size: Optional[int] = None
) -> SearchStrategy:
    """
    A strategy which generates ASCII-encodable text.
    """
    return text(
        min_size=min_size, max_size=max_size, alphabet=printable
    )


def user_names() -> SearchStrategy:
    """
    A strategy which generates user names.
    """
    return text(
        min_size=1, max_size=256, alphabet=ascii_letters + digits + "_-"
    )


def host_names() -> SearchStrategy:
    """
    A strategy which generates host names.
    """
    return text(min_size=1, max_size=256, alphabet="0123456789abcdef")


def port_numbers() -> SearchStrategy:
    """
    A strategy which generates port numbers.
    """
    return integers(min_value=1, max_value=65535)


def commitIDs() -> SearchStrategy:
    """
    A strategy which generates Git commit IDs.
    """
    return text(
        min_size=40, max_size=40,
        alphabet=ascii_letters + digits,
    )


@composite
def email_addresses(draw: Callable) -> str:
    """
    A strategy which generates email addresses.
    """
    return f"{draw(user_names())}@{draw(host_names())}"


@composite
def repository_ids(draw: Callable) -> str:
    """
    A strategy which generates GitHub repository IDs.
    """
    return f"{draw(user_names())}/{draw(user_names())}"
