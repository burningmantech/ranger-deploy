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
Tests for :mod:`deploy.ext.click`
"""

from pathlib import Path
from re import findall
from sys import maxunicode

from hypothesis import given, note
from hypothesis.strategies import characters, dictionaries, text
from twisted.trial.unittest import SynchronousTestCase as TestCase

from ..click import readConfig


__all__ = ()


unicodeWhitespace = "".join(
    findall(r"\s", "".join(chr(c) for c in range(maxunicode + 1)))
)


configExcludeCategories = (
    "Cc",  # Control
    "Cf",  # Format
    "Cn",  # Not assigned
    "Co",  # Private use
    "Cs",  # Surrogate (default)
    "Zl",  # Line separator
    "Zp",  # Paragraph separator
)


class ReadConfigTests(TestCase):
    """
    Tests for :func:`readConfig`
    """

    @given(
        text(  # profile
            min_size=1,
            alphabet=characters(
                exclude_categories=configExcludeCategories  # type:ignore[arg-type]
                + ("Zs",),  # Spaces
                blacklist_characters="]",
            ),
        ),
        dictionaries(  # config keys
            text(
                min_size=1,
                alphabet=characters(
                    exclude_categories=configExcludeCategories,  # type:ignore[arg-type]
                    blacklist_characters="=",
                ),
            ),
            text(  # config values
                alphabet=characters(
                    exclude_categories=configExcludeCategories  # type:ignore[arg-type]
                ),
            ),
        ),
    )
    def test_readConfig(self, profile: str, configDict: dict[str, str]) -> None:
        # Normalize the config dict so that we ensure keys and valid and that
        # we can can compare this dict with the result:
        #  * Keys and values are stripped of leading and trailing whitespace.
        #  * Keys are lowercased.
        #  * Keys are prefixed with "x" to ensure that they are not empty and
        #    don't start with a comment character.
        #  * Leading "="s are removed from values.
        #  * "$" is removed from values so that we don't trigger interpolation.
        configDict = {
            f"x{k.lower().strip()}": (
                v.replace("$", "").lstrip(unicodeWhitespace + "=").rstrip()
            )
            for k, v in configDict.items()
        }

        configLines = [f"[{profile}]\n"]
        for key, value in configDict.items():
            configLines.append(f"{key} = {value}\n")

        configText = "\n".join(configLines) + "\n"

        note(configText)

        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)

        resultConfig = readConfig(profile=profile, path=configFilePath)

        self.assertEqual(resultConfig, configDict)

    def test_readConfig_noProfile(self) -> None:
        configText = ""

        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)

        self.assertEqual(readConfig(profile="foo", path=configFilePath), {})

    def test_readConfig_default(self) -> None:
        configText = "[default]\nfoo = bar\n"

        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)

        self.assertEqual(readConfig(path=configFilePath), {"foo": "bar"})

    def test_readConfig_none(self) -> None:
        configText = "[default]\nfoo = bar\n"

        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)

        self.assertEqual(
            readConfig(profile=None, path=configFilePath), {"foo": "bar"}
        )
