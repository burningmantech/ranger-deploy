##
# See the file COPYRIGHT for copyright information.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Tests for :mod:`deploy.ext.json`
"""

from datetime import date as Date
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from datetime import timezone as TimeZone
from io import BytesIO
from json import JSONDecodeError
from textwrap import dedent
from types import MappingProxyType
from typing import Any, Callable

from hypothesis import given
from hypothesis.strategies import SearchStrategy, composite, dates
from hypothesis.strategies import datetimes as _datetimes
from hypothesis.strategies import dictionaries, integers, lists, one_of, text
from twisted.trial.unittest import SynchronousTestCase as TestCase

from ..json import (
    dateAsRFC3339Text,
    dateTimeAsRFC3339Text,
    jsonTextFromObject,
    objectFromJSONBytesIO,
    objectFromJSONText,
    rfc3339TextAsDate,
    rfc3339TextAsDateTime,
)


__all__ = ()


@composite
def timezones(draw: Callable) -> TimeZone:
    offset = draw(integers(min_value=-(60 * 24) + 1, max_value=(60 * 24) - 1))
    timeDelta = TimeDelta(minutes=offset)
    timeZone = TimeZone(offset=timeDelta, name=f"{offset}s")
    return timeZone


def datetimes() -> SearchStrategy:
    return _datetimes(timezones=timezones())


def json() -> SearchStrategy:
    return one_of(
        integers(),
        text(),
        lists(integers()),
        lists(text()),
        dictionaries(text(), integers()),
        dictionaries(text(), text()),
    )


class JSONEncodingTests(TestCase):
    """
    Tests for :func:`jsonTextFromObject`
    """

    @given(lists(json()))
    def test_encodeIterables(self, items: list[Any]) -> None:
        """
        :func:`jsonTextFromObject` encodes iterables other than :class:`list`
        and :class:`tuple`.

        This indirectly tests :class:`..json.Encoder`.
        """
        self.assertEqual(
            jsonTextFromObject(iter(items)),
            jsonTextFromObject(items),
        )

    @given(dictionaries(text(), json()))
    def test_encodeMappings(self, items: dict[str, Any]) -> None:
        """
        :func:`jsonTextFromObject` encodes mappings other than :class:`dict`.

        This indirectly tests :class:`..json.Encoder`.
        """
        self.assertEqual(
            jsonTextFromObject(MappingProxyType(items)),
            jsonTextFromObject(items),
        )

    @given(datetimes())
    def test_encodeDateTimes(self, dateTime: DateTime) -> None:
        """
        :func:`jsonTextFromObject` encodes mappings other than :class:`dict`.

        This indirectly tests :class:`..json.Encoder`.
        """
        self.assertEqual(
            jsonTextFromObject(dateTime),
            f'"{dateTimeAsRFC3339Text(dateTime)}"',  # noqa: B028
        )

    def test_encodeUnknown(self) -> None:
        """
        :func:`jsonTextFromObject` raises :exc:`TypeError` when given an
        unknown object type.

        This indirectly tests :class:`config_service.util.json.Encoder`.
        """
        self.assertRaises(TypeError, jsonTextFromObject, object())

    def test_jsonTextFromObject_ugly(self) -> None:
        """
        :func:`jsonTextFromObject` encodes JSON without pretty-ness if
        :obj:`pretty` is false.
        """
        obj = dict(x="Hello", y=["one", "two", "three"])

        self.assertEqual(
            jsonTextFromObject(obj, pretty=False),
            '{"x":"Hello","y":["one","two","three"]}',
        )

    def test_jsonTextFromObject_pretty(self) -> None:
        """
        :func:`jsonTextFromObject` encodes JSON with pretty-ness if
        :obj:`pretty` is true.
        """
        obj = dict(x="Hello", y=["one", "two", "three"])

        self.assertEqual(
            jsonTextFromObject(obj, pretty=True),
            dedent(
                """
                {
                  "x": "Hello",
                  "y": [
                    "one",
                    "two",
                    "three"
                  ]
                }
                """
            )[1:-1],
        )


class JSONDecodingTests(TestCase):
    """
    Tests for :func:`objectFromJSONText`
    """

    def test_objectFromJSONText(self) -> None:
        """
        :func:`objectFromJSONText` decodes JSON into POPOs.
        """
        self.assertEqual(
            objectFromJSONText(
                """
                {
                  "x": "Hello",
                  "y": [
                    "one",
                    "two",
                    "three"
                  ]
                }
                """
            ),
            dict(x="Hello", y=["one", "two", "three"]),
        )

    def test_objectFromJSONText_badInput(self) -> None:
        """
        :func:`objectFromJSONText` raises :exc:`JSONDecodeError` then given
        invalid JSON text.
        """
        self.assertRaises(JSONDecodeError, objectFromJSONText, "foo}")

    def test_objectFromJSONBytesIO(self) -> None:
        """
        :func:`objectFromJSONBytesIO` decodes JSON into POPOs.
        """
        self.assertEqual(
            objectFromJSONBytesIO(
                BytesIO(
                    """
                    {
                      "x": "Hello",
                      "y": [
                        "one",
                        "two",
                        "three"
                      ]
                    }
                    """.encode(
                        "ascii"
                    )
                )
            ),
            dict(x="Hello", y=["one", "two", "three"]),
        )

    def test_objectFromJSONBytesIO_badInput(self) -> None:
        """
        :func:`objectFromJSONBytesIO` raises :exc:`JSONDecodeError` then given
        invalid JSON text.
        """
        self.assertRaises(
            JSONDecodeError,
            objectFromJSONBytesIO,
            BytesIO(b"foo}"),
        )


class DateTimeTests(TestCase):
    """
    Test for encoding and decoding of date/time objects.
    """

    @staticmethod
    def dateAsRFC3339Text(date: Date) -> str:
        """
        Convert a :class:`Date` into an RFC 3339 formatted date string.

        :param date: A date to convert.

        :return: An RFC 3339 formatted date string corresponding to
        :obj:`date`.
        """
        return f"{date.year:04d}-{date.month:02d}-{date.day:02d}"

    @staticmethod
    def dateTimeAsRFC3339Text(dateTime: DateTime) -> str:
        """
        Convert a :class:`DateTime` into an RFC 3339 formatted date-time
        string.

        :param datetime: A non-naive :class:`DateTime` to convert.

        :return: An RFC 3339 formatted date-time string corresponding to
            :obj:`datetime`.
        """
        timeZone = dateTime.tzinfo
        assert timeZone is not None

        offset = timeZone.utcoffset(dateTime)
        assert offset is not None

        utcOffset = offset.total_seconds()

        if dateTime.microsecond == 0:
            microsecond = ""
        else:
            microsecond = f".{dateTime.microsecond:06d}"

        tzHour = int(utcOffset / 60 / 60)
        tzMinute = int(utcOffset / 60) % 60
        if utcOffset < 0:
            tzSign = "-"
            tzHour *= -1
            tzMinute = (60 - tzMinute) % 60
        else:
            tzSign = "+"

        return (
            f"{dateTime.year:04d}-{dateTime.month:02d}-{dateTime.day:02d}"
            "T"
            f"{dateTime.hour:02d}:{dateTime.minute:02d}:{dateTime.second:02d}"
            f"{microsecond}"
            f"{tzSign}{tzHour:02d}:{tzMinute:02d}"
        )

    @given(dates())
    def test_dateAsRFC3339Text(self, date: Date) -> None:
        """
        :func:`dateAsRFC3339Text` converts a :class:`Date` into a RFC 3339
        formatted date string.
        """
        self.assertEqual(dateAsRFC3339Text(date), self.dateAsRFC3339Text(date))

    @given(dates())
    def test_rfc3339TextAsDate(self, date: Date) -> None:
        """
        :func:`rfc3339TextAsDate` converts a RFC 3339 formatted date string
        into a :class:`Date`.
        """
        self.assertEqual(rfc3339TextAsDate(self.dateAsRFC3339Text(date)), date)

    @given(datetimes())
    def test_dateTimeAsRFC3339Text(self, dateTime: DateTime) -> None:
        """
        :func:`dateTimeAsRFC3339Text` converts a :class:`DateTime` into a RFC
        3339 formatted date string.
        """
        self.assertEqual(
            dateTimeAsRFC3339Text(dateTime),
            self.dateTimeAsRFC3339Text(dateTime),
        )

    @given(datetimes())
    def test_rfc3339TextAsDateTime(self, dateTime: DateTime) -> None:
        """
        :func:`rfc3339TextAsDateTime` converts a RFC 3339 formatted date-time
        string into a :class:`DateTime`.
        """
        self.assertEqual(
            rfc3339TextAsDateTime(self.dateTimeAsRFC3339Text(dateTime)),
            dateTime,
        )
