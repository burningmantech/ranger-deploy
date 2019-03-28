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

from contextlib import contextmanager
from email.message import Message
from ssl import SSLContext
from typing import Any, ClassVar, Iterator, List, Optional, Tuple, Type, cast

from attr import Factory, attrs

from hypothesis import given

from twisted.trial.unittest import SynchronousTestCase as TestCase

from deploy.ext.click import clickTestRun
from deploy.ext.hypothesis import (
    ascii_text, email_addresses,
    port_numbers, repository_ids, user_names,
)

from .. import smtp
from ..smtp import SMTPNotifier


__all__ = ()



@attrs(auto_attribs=True)
class MockSMTPServer(object):
    _logins: List[Tuple[str, str]] = Factory(list)
    _messages: List[Tuple[str, str, Message]] = Factory(list)


    def login(self, user: str, password: str) -> None:
        self._logins.append((user, password))


    def send_message(
        self, message: Message, sender: str, recipient: str
    ) -> None:
        assert self._logins
        self._messages.append((sender, recipient, message))



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



@contextmanager
def testingSMTP() -> Iterator[None]:
    SMTP_SSL = smtp.SMTP_SSL
    smtp.SMTP_SSL = cast(Type, MockSMTPSSL)

    yield None

    smtp.SMTP_SSL = SMTP_SSL
    MockSMTPSSL._instances.clear()



class SMTPNotifierTests(TestCase):
    """
    Tests for :class:`SMTPNotifier`
    """

    @given(
        ascii_text(min_size=1),  # smtpHost
        port_numbers(),          # smtpPort
        user_names(),            # smtpUser
        ascii_text(min_size=1),  # smtpPassword
        email_addresses(),       # senderAddress
        email_addresses(),       # recipientAddress
        ascii_text(min_size=1),  # project
        repository_ids(),        # repositoryOrganization
        ascii_text(min_size=1),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        ascii_text(min_size=1),  # commitID
        ascii_text(min_size=1),  # commitMessage  FIXME: use text()
    )
    def test_notifyStaging(
        self,
        smtpHost: str, smtpPort: int,
        smtpUser: str, smtpPassword: str,
        senderAddress: str, recipientAddress: str,
        project: str, repository: str,
        buildNumber: str, buildURL: str,
        commitID: str, commitMessage: str,
    ) -> None:
        # Because hypothesis and multiple runs
        with testingSMTP():
            notifier = SMTPNotifier(
                smtpHost=smtpHost, smtpPort=smtpPort,
                smtpUser=smtpUser, smtpPassword=smtpPassword,
                senderAddress=senderAddress, recipientAddress=recipientAddress,
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
                server._messages[0][0:2], (senderAddress, recipientAddress)
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
                f"Travis build #{buildNumber} for commit {commitID} has "
                f"completed successfully and the resulting image has been "
                f"deployed to the staging environment.\n"
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



class CommandLineTests(TestCase):
    """
    Tests for the :class:`SMTPNotifier` command line.
    """

    @given(
        ascii_text(min_size=1),  # smtpHost
        port_numbers(),          # smtpPort
        user_names(),            # smtpUser
        ascii_text(min_size=1),  # smtpPassword
        email_addresses(),       # senderAddress
        email_addresses(),       # recipientAddress
        ascii_text(min_size=1),  # project
        repository_ids(),        # repositoryOrganization
        ascii_text(min_size=1),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        ascii_text(min_size=1),  # commitID
        ascii_text(min_size=1),  # commitMessage  FIXME: use text()
    )
    def test_staging(
        self,
        smtpHost: str, smtpPort: int,
        smtpUser: str, smtpPassword: str,
        senderAddress: str, recipientAddress: str,
        project: str, repository: str,
        buildNumber: str, buildURL: str,
        commitID: str, commitMessage: str,
    ) -> None:
        with testingSMTP():
            result = clickTestRun(
                SMTPNotifier.main,
                [
                    "notify_smtp", "staging",
                    "--project-name", project,
                    "--repository-id", repository,
                    "--build-number", buildNumber,
                    "--build-url", buildURL,
                    "--commit-id", commitID,
                    "--commit-message", commitMessage,
                    "--smtp-host", smtpHost,
                    "--smtp-port", str(smtpPort),
                    "--smtp-user", smtpUser,
                    "--smtp-password", smtpPassword,
                    "--sender", senderAddress,
                    "--recipient", recipientAddress,
                ],
            )
            self.assertEqual(result.exitCode, 0)
            self.assertEqual(result.echoOutput, [])
