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
from typing import Callable

from hypothesis import assume, given, note
from hypothesis.strategies import (
    SearchStrategy,
    characters,
    composite,
    dictionaries,
    text,
)

from twisted.trial.unittest import SynchronousTestCase as TestCase

from ..click import ConfigDict, readConfig


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


def profileNames(allowDefault: bool = True) -> SearchStrategy:
    strategy = text(
        min_size=1,
        alphabet=characters(
            blacklist_categories=configBlacklistCategories + ("Zs",),  # Spaces
            blacklist_characters="]",
        ),
    )

    if not allowDefault:
        strategy = strategy.filter(lambda s: s != "default")

    return strategy


@composite
def configKeys(draw: Callable) -> str:
    key: str = draw(
        text(
            min_size=1,
            alphabet=characters(
                blacklist_categories=configBlacklistCategories,
                blacklist_characters="=",
            ),
        )
    )
    key = key.strip().lstrip("#")
    assume(key)
    return key


@composite
def configValues(draw: Callable) -> str:
    value: str = draw(
        text(
            alphabet=characters(blacklist_categories=configBlacklistCategories)
        )
    )

    # No variable interpolation
    value = value.replace("$", "")

    # No leading or trailing whitespace
    value = value.strip()

    # Don't start with comment or =
    if value:
        assume(value[0] not in "#=")

    return value


@composite
def configDicts(draw: Callable) -> ConfigDict:
    configDict = draw(dictionaries(configKeys(), configValues()))

    # Normalize the config dict so that we ensure keys are valid and that
    # we can can compare this dict with the result:
    #  * Keys are lowercased.
    #  * Values are prefixed with "x" if they start with a comment character
    #  ("#") or an equal sign ("=")
    #  * "$" is removed from values so that we don't trigger interpolation.

    return {key.lower(): value for key, value in configDict.items()}


def textFromConfig(profile: str, configDict: ConfigDict) -> str:
    configLines = [f"[{profile}]\n"]
    for key, value in configDict.items():
        configLines.append(f"{key} = {value}\n")

    return "".join(configLines)


class ReadConfigTests(TestCase):
    """
    Tests for :func:`readConfig`
    """

    def writeConfig(self, configText: str) -> Path:
        configFilePath = Path(self.mktemp())
        with configFilePath.open("w") as configFile:
            configFile.write(configText)
        return configFilePath

    @given(profileNames(), configDicts())
    def test_readConfig(self, profile: str, configDict: ConfigDict) -> None:
        """
        Configuration text is properly parsed into a config dict.
        """
        configText = textFromConfig(profile, configDict)

        note(configText)

        configFilePath = self.writeConfig(textFromConfig(profile, configDict))

        resultConfig = readConfig(profile=profile, path=configFilePath)

        self.assertEqual(resultConfig, configDict)

    @given(profileNames(allowDefault=False))
    def test_readConfig_noProfile(self, profile: str) -> None:
        """
        If the selected profile doesn't exist, we get an empty configuration.
        """
        configFilePath = self.writeConfig("")

        self.assertEqual(readConfig(profile=profile, path=configFilePath), {})

    @given(configDicts())
    def test_readConfig_default(self, configDict: ConfigDict) -> None:
        """
        When no profile is selected, the default profile provides the
        configuration.
        """
        configText = textFromConfig("default", configDict)

        configFilePath = self.writeConfig(configText)

        self.assertEqual(readConfig(path=configFilePath), configDict)

    def test_readConfig_default_mix(self) -> None:
        """
        Values from the default profile properly mix into the selected profile.
        """
        configText = (
            "[default]\n"
            "foo = FOO\n"
            "bar = BAR_1\n"
            "[two]\n"
            "bar = BAR_2\n"
            "baz = BAZ\n"
        )

        configFilePath = self.writeConfig(configText)

        self.assertEqual(
            readConfig(profile="two", path=configFilePath),
            {"foo": "FOO", "bar": "BAR_2", "baz": "BAZ"},
        )

    def test_readConfig_none(self) -> None:
        configText = "[default]\nfoo = bar\n"

        configFilePath = self.writeConfig(configText)

        self.assertEqual(
            readConfig(profile=None, path=configFilePath), {"foo": "bar"}
        )
