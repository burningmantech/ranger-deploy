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
from typing import Dict

from hypothesis import assume, given, note
from hypothesis.strategies import characters, dictionaries, text

from twisted.trial.unittest import SynchronousTestCase as TestCase

from ..click import readConfig


__all__ = ()


configBlacklistCategories = (
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
                blacklist_categories=configBlacklistCategories + (
                    "Zs",  # Spaces
                ),
            ),
        ),
        dictionaries(  # config keys
            text(
                min_size=1,
                alphabet=characters(
                    blacklist_categories=configBlacklistCategories
                ),
            ),
            text(  # config values
                alphabet=characters(
                    blacklist_categories=configBlacklistCategories
                ),
            ),
        ),
    )
    def test_readConfig(
        self, profile: str, configDict: Dict[str, str]
    ) -> None:
        assume("]" not in profile)  # No "]" in profile

        configLines = [f"[{profile}]\n"]
        for key, value in configDict.items():
            assume("=" not in key)       # No "=" in key
            assume(key[0] not in "# ")   # No leading space or comment
            assume("$" not in value)     # Don't trigger interpolation

            configLines.append(f"{key} = {value}\n")

        configText = "\n".join(configLines) + "\n"

        note(configText)

        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)

        resultConfig = readConfig(profile=profile, path=configFilePath)

        self.assertEqual(
            resultConfig,
            {
                k.lower().strip(): v.strip() for k, v in configDict.items()
            },
        )


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
