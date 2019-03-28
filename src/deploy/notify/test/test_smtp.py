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
Tests for :mod:`deploy.notify.smtp`
"""

from email.message import Message
from ssl import SSLContext
from string import ascii_letters, digits
from typing import (
    Any, Callable, ClassVar, List, Optional, Tuple, TypeVar, cast
)

from attr import Factory, attrs

from hypothesis import given
from hypothesis.strategies import integers, text

from twisted.trial.unittest import SynchronousTestCase as TestCase

from .. import smtp
from ..smtp import SMTPNotifier


__all__ = ()


T = TypeVar('T')
DrawCallable = Callable[[Callable[..., T]], T]


def ascii_text(
    min_size: Optional[int] = 0, max_size: Optional[int] = None
) -> str:
    """
    A strategy which generates ASCII-encodable text.
    """
    return text(
        min_size=min_size, max_size=max_size, alphabet=(ascii_letters + digits)
    )


@attrs(auto_attribs=True)
class MockSMTPServer(object):
    _logins: List[Tuple[str, str]] = Factory(list)
    _messages: List[Tuple[str, str, Message]] = Factory(list)


    def login(self, user: str, password: str) -> None:
        self._logins.append((user, password))


    def send_message(
        self, message: Message, sender: str, destination: str
    ) -> None:
        assert self._logins
        self._messages.append((sender, destination, message))



@attrs(auto_attribs=True)
class MockSMTPSSL(object):
    _instances: ClassVar[List["MockSMTPSSL"]] = []

    host: str
    port: int
    context: Optional[SSLContext] = None

    _servers: List[MockSMTPServer] = Factory(list)


    def __attrs_post_init__(self) -> None:
        self._instances.append(self)


    def __enter__(self) -> MockSMTPServer:
        server = MockSMTPServer()
        self._servers.append(server)
        return server


    def __exit__(self, *args: Any) -> None:
        pass



class SMTPNotifierTests(TestCase):
    """
    Tests for :class:`SMTPNotifier`
    """

    def setUp(self) -> None:
        self.patch(smtp, "SMTP_SSL", MockSMTPSSL)


    def tearDown(self) -> None:
        MockSMTPSSL._instances.clear()


    @given(  # FIXME: Should use text() for commitMessage
        ascii_text(),                            # smtpPort
        integers(min_value=1, max_value=65535),  # smtpHost
        ascii_text(), ascii_text(),    # smtpUser, smtpPassword
        ascii_text(), ascii_text(),    # senderAddress, destinationAddress
        ascii_text(), ascii_text(),    # project, repository
        ascii_text(), ascii_text(),    # buildNumber, buildURL
        ascii_text(), ascii_text(),    # commitID, commitMessage
    )
    def test_notifyStaging(
        self,
        smtpHost: str, smtpPort: int,
        smtpUser: str, smtpPassword: str,
        senderAddress: str, destinationAddress: str,
        project: str, repository: str,
        buildNumber: str, buildURL: str,
        commitID: str, commitMessage: str,
    ) -> None:
        # Because hypothesis and multiple runs
        MockSMTPSSL._instances.clear()

        notifier = SMTPNotifier(
            smtpHost=smtpHost, smtpPort=smtpPort,
            smtpUser=smtpUser, smtpPassword=smtpPassword,
            senderAddress=senderAddress, destinationAddress=destinationAddress,
        )
        notifier.notifyStaging(
            project=project, repository=repository,
            buildNumber=buildNumber, buildURL=buildURL,
            commitID=commitID, commitMessage=commitMessage,
        )

        self.assertEqual(len(MockSMTPSSL._instances), 1)
        instance = MockSMTPSSL._instances[0]

        self.assertEqual(len(instance._servers), 1)
        server = instance._servers[0]

        self.assertEqual(instance.host, smtpHost)
        self.assertEqual(instance.port, smtpPort)
        self.assertIsInstance(instance.context, SSLContext)
        self.assertEqual(server._logins, [(smtpUser, smtpPassword)])

        self.assertEqual(len(server._messages), 1)
        self.assertEqual(
            server._messages[0][0:2], (senderAddress, destinationAddress)
        )

        message = server._messages[0][2]
        self.assertTrue(message.is_multipart())

        parts = cast(List[Message], message.get_payload())
        self.assertTrue(len(parts), 2)

        title = f"{project} Deployed to Staging"
        commitURL = f"https://github.com/{repository}/commit/{commitID}"

        expectedText = (
            f"{title}\n"
            f"\n"
            f"Travis build #{buildNumber} for commit {commitID} has completed "
            f"successfully and the resulting image has been deployed to the "
            f"staging environment.\n"
            f"\n"
            f"{commitMessage}\n"
            f"\n"
            f"Diff: {commitURL}\n"
            f"Build log: {buildURL}\n"
        )

        expectedHTML = (
            f"<html>\n"
            f"  <head>{title}</head>\n"
            f"<body>\n"
            f"\n"
            f"  <h1>{title}</h1>\n"
            f"\n"
            f"  <p>\n"
            f"    <a href=\"{buildURL}\">Travis build #{buildNumber}</a>\n"
            f"    for <a href=\"{commitURL}\">commit {commitID}</a>\n"
            f"    has completed successfully and the resulting image has\n"
            f"    been deployed to the staging environment.\n"
            f"  </p>\n"
            f"\n"
            f"  <blockquote>{commitMessage}</blockquote>\n"
            f"</body>\n"
            f"</html>\n"
        )

        self.maxDiff = None
        for part in parts:
            contentType = part.get_content_type()
            payload = part.get_payload()

            if contentType == "text/plain":
                self.assertEqual(payload, expectedText)
            elif contentType == "text/html":
                self.assertEqual(payload, expectedHTML)
            else:  # pragma: no cover
                raise AssertionError(f"Unexpected content type: {payload}")