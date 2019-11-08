"""
Extensions to :mod:`json`
"""

from datetime import date as Date, datetime as DateTime
from io import TextIOWrapper
from json import (
    JSONDecodeError, JSONEncoder as BaseJSONEncoder, dumps, load, loads
)
from typing import Any, BinaryIO, Optional, cast

from arrow.parser import DateTimeParser


__all__ = (
    "dateAsRFC3339Text",
    "dateTimeAsRFC3339Text",
    "jsonTextFromObject",
    "objectFromJSONText",
    "rfc3339TextAsDate",
    "rfc3339TextAsDateTime",
)



class JSONEncoder(BaseJSONEncoder):
    """
    JSON encoder that attempts to convert :class:`Mapping` to :class:`dict`,
    and other types of :class:`Iterable` to :class:`list`.
    """

    def default(self, obj: Any) -> Any:
        iterate = getattr(obj, "__iter__", None)
        if iterate is not None:
            # We have an Iterable
            if hasattr(obj, "__getitem__"):
                # We have a Mapping
                return dict(obj)
            return list(iterate())

        if isinstance(obj, DateTime):
            return dateAsRFC3339Text(obj)

        return BaseJSONEncoder.default(self, obj)



def jsonTextFromObject(obj: Any, pretty: bool = False) -> str:
    """
    Convert an object into JSON text.

    :param obj: An object that is serializable to JSON.

    :param pretty: Whether to format for easier human consumption.
    """
    if pretty:
        separators = (",", ": ")
        indent: Optional[int] = 2
        sortKeys = True
    else:
        separators = (",", ":")
        indent = None
        sortKeys = False

    return dumps(
        obj,
        ensure_ascii=False,
        separators=separators,
        indent=indent,
        sort_keys=sortKeys,
        cls=JSONEncoder,
    )



def objectFromJSONText(text: str) -> Any:
    """
    Convert JSON text into an object.
    """
    try:
        return loads(text)
    except JSONDecodeError as e:
        raise JSONDecodeError(
            msg=f"{e.msg} in {text!r}",
            doc=e.doc,
            pos=e.pos,
        )



def objectFromJSONBytesIO(io: BinaryIO, encoding: str = "utf-8") -> Any:
    """
    Covert JSON text from a byte stream into an object.
    """
    textIO = TextIOWrapper(io, encoding=encoding, newline="")
    return load(textIO)



def dateAsRFC3339Text(date: Date) -> str:
    """
    Convert a :class:`Date` into an RFC 3339 formatted date string.

    :param date: A date to convert.

    :return: An RFC 3339 formatted date string corresponding to :obj:`date`.
    """
    return date.isoformat()



def rfc3339TextAsDate(rfc3339: str) -> Date:
    """
    Convert an RFC 3339 formatted string to a :class:`Date`.

    :param rfc3339: An RFC-3339 formatted string.

    :return: An :class:`Date` corresponding to :obj:`rfc3339`.
    """
    return cast(Date, DateTimeParser().parse_iso(rfc3339).date())



def dateTimeAsRFC3339Text(dateTime: DateTime) -> str:
    """
    Convert a :class:`DateTime` into an RFC 3339 formatted date-time string.

    :param dateTime: A non-naive :class:`DateTime` to convert.

    :return: An RFC 3339 formatted date-time string corresponding to
        :obj:`dateTime`.
    """
    return dateTime.isoformat()



def rfc3339TextAsDateTime(rfc3339: str) -> DateTime:
    """
    Convert an RFC 3339 formatted string to a :class:`DateTime`.

    :param rfc3339: An RFC-3339 formatted string.

    :return: A :class:`DateTime` corresponding to :obj:`rfc3339`.
    """
    return cast(DateTime, DateTimeParser().parse_iso(rfc3339))


jsonTrue  = jsonTextFromObject(True)
jsonFalse = jsonTextFromObject(False)
