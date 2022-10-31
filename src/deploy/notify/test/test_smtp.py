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
from ssl import SSLContext, SSLError
from typing import Any, ClassVar, Iterator, List, Optional, Tuple, Type, cast
from unittest.mock import patch

from attr import Factory, attrs
from hypothesis import given
from hypothesis.strategies import booleans
from twisted.trial.unittest import SynchronousTestCase as TestCase

from deploy.ext.click import clickTestRun
from deploy.ext.hypothesis import (
    ascii_text,
    commitIDs,
    email_addresses,
    port_numbers,
    repository_ids,
    user_names,
)

from .. import smtp
from ..smtp import SMTPNotifier


__all__ = ()


@attrs(auto_attribs=True)
class MockSMTPServer:
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
class MockSMTPSSL:
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
    smtp.SMTP_SSL = cast(Type, MockSMTPSSL)  # type: ignore[misc]

    try:
        yield None

    finally:
        smtp.SMTP_SSL = SMTP_SSL  # type: ignore[misc]
        MockSMTPSSL._instances.clear()


class SMTPNotifierTests(TestCase):
    """
    Tests for :class:`SMTPNotifier`
    """

    @given(
        ascii_text(min_size=1),  # smtpHost
        port_numbers(),  # smtpPort
        user_names(),  # smtpUser
        ascii_text(min_size=1),  # smtpPassword
        email_addresses(),  # senderAddress
        email_addresses(),  # recipientAddress
        ascii_text(min_size=1),  # project
        repository_ids(),  # repository
        ascii_text(min_size=1),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        commitIDs(),  # commitID
        ascii_text(min_size=1),  # commitMessage  FIXME: use text()
    )
    def test_notifyStaging(
        self,
        smtpHost: str,
        smtpPort: int,
        smtpUser: str,
        smtpPassword: str,
        senderAddress: str,
        recipientAddress: str,
        project: str,
        repository: str,
        buildNumber: str,
        buildURL: str,
        commitID: str,
        commitMessage: str,
    ) -> None:
        with testingSMTP():
            notifier = SMTPNotifier(
                smtpHost=smtpHost,
                smtpPort=smtpPort,
                smtpUser=smtpUser,
                smtpPassword=smtpPassword,
                senderAddress=senderAddress,
                recipientAddress=recipientAddress,
            )
            notifier.notifyStaging(
                project=project,
                repository=repository,
                buildNumber=buildNumber,
                buildURL=buildURL,
                commitID=commitID,
                commitMessage=commitMessage,
                trialRun=False,
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

            commitIDShort = commitID[:7]

            expectedText = (
                f"{title}\n"
                f"\n"
                f"Build #{buildNumber} for commit {commitIDShort} has "
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
                f'    <a href="{buildURL}">Build #{buildNumber}</a>\n'
                f'    for <a href="{commitURL}">commit {commitIDShort}</a>\n'
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
                    raise AssertionError(
                        f"Unexpected content type: {payload!r}"
                    )


class CommandLineTests(TestCase):
    """
    Tests for the :class:`SMTPNotifier` command line.
    """

    @given(
        ascii_text(min_size=1),  # smtpHost
        port_numbers(),  # smtpPort
        user_names(),  # smtpUser
        ascii_text(min_size=1),  # smtpPassword
        email_addresses(),  # senderAddress
        email_addresses(),  # recipientAddress
        ascii_text(min_size=1),  # project
        repository_ids(),  # repository
        ascii_text(min_size=1),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        commitIDs(),  # commitID
        ascii_text(min_size=1),  # commitMessage  FIXME: use text()
        booleans(),  # trialRun
    )
    def test_staging(
        self,
        smtpHost: str,
        smtpPort: int,
        smtpUser: str,
        smtpPassword: str,
        senderAddress: str,
        recipientAddress: str,
        project: str,
        repository: str,
        buildNumber: str,
        buildURL: str,
        commitID: str,
        commitMessage: str,
        trialRun: bool,
    ) -> None:
        args = [
            "notify_smtp",
            "staging",
            "--project-name",
            project,
            "--repository-id",
            repository,
            "--build-number",
            buildNumber,
            "--build-url",
            buildURL,
            "--commit-id",
            commitID,
            "--commit-message",
            commitMessage,
            "--smtp-host",
            smtpHost,
            "--smtp-port",
            str(smtpPort),
            "--smtp-user",
            smtpUser,
            "--smtp-password",
            smtpPassword,
            "--email-sender",
            senderAddress,
            "--email-recipient",
            recipientAddress,
        ]

        if trialRun:
            args.append("--trial-run")

        with patch(
            "deploy.notify.smtp.SMTPNotifier.notifyStaging"
        ) as notifyStaging:
            result = clickTestRun(SMTPNotifier.main, args)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

        self.assertEqual(notifyStaging.call_count, 1)
        args, kwargs = notifyStaging.call_args
        self.assertEqual(args, ())
        self.assertEqual(
            kwargs,
            dict(
                project=project,
                repository=repository,
                buildNumber=buildNumber,
                buildURL=buildURL,
                commitID=commitID,
                commitMessage=commitMessage,
                trialRun=trialRun,
            ),
        )

    def test_staging_noProject(self) -> None:
        """
        If --project-name is not given, the project name is derived from the
        repository ID.
        """
        with patch(
            "deploy.notify.smtp.SMTPNotifier.notifyStaging"
        ) as notifyStaging:
            result = clickTestRun(
                SMTPNotifier.main,
                [
                    "notify_smtp",
                    "staging",
                    "--repository-id",
                    "some-org/some-project",
                    "--build-number",
                    "build-number",
                    "--build-url",
                    "http://example.com/",
                    "--commit-id",
                    "101010",
                    "--commit-message",
                    "Hello",
                    "--smtp-host",
                    "mail.example.com",
                    "--smtp-user",
                    "user",
                    "--smtp-password",
                    "password",
                    "--email-sender",
                    "sender@example.com",
                    "--email-recipient",
                    "recipient@example.com",
                ],
            )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(notifyStaging.call_count, 1)
        args, kwargs = notifyStaging.call_args
        self.assertEqual(kwargs["project"], "some-project")

    def test_staging_badRepository(self) -> None:
        """
        A bad repository ID results in a usage error.
        """
        with patch("deploy.notify.smtp.SMTPNotifier.notifyStaging"):
            result = clickTestRun(
                SMTPNotifier.main,
                [
                    "notify_smtp",
                    "staging",
                    "--repository-id",
                    "some-org/some-project/garbage",
                    "--build-number",
                    "build-number",
                    "--build-url",
                    "http://example.com/",
                    "--commit-id",
                    "101010",
                    "--commit-message",
                    "Hello",
                    "--smtp-host",
                    "mail.example.com",
                    "--smtp-user",
                    "user",
                    "--smtp-password",
                    "password",
                    "--email-sender",
                    "sender@example.com",
                    "--email-recipient",
                    "recipient@example.com",
                ],
            )

        self.assertEqual(result.exitCode, 2)
        self.assertEqual(result.stdout.getvalue(), "")

        errors = result.stderr.getvalue()

        # Note "Invalid value" line below sometimes generates different quotes
        expectedErrors_start = "Usage: notify_smtp staging [OPTIONS]\n"
        expectedErrors_end = (
            "Invalid repository ID: some-org/some-project/garbage\n"
        )
        expectedErrors = (
            expectedErrors_start
            + "Error: Invalid value for '--repository-id': "
            + expectedErrors_end
        )

        try:
            self.assertTrue(errors.startswith(expectedErrors_start))
            self.assertTrue(errors.endswith(expectedErrors_end))
        except self.failureException:  # pragma: no cover
            # This will print a more useful error
            self.assertEqual(errors, expectedErrors)

    def test_staging_sslError(self) -> None:
        """
        SSL error raised while connecting to mail server.
        """

        def SMTP_SSL(*args: object, **kwargs: object) -> None:
            raise SSLError("SSL Oopsie")

        with patch("deploy.notify.smtp.SMTP_SSL", SMTP_SSL):
            result = clickTestRun(
                SMTPNotifier.main,
                [
                    "notify_smtp",
                    "staging",
                    "--repository-id",
                    "some-org/some-project",
                    "--build-number",
                    "build-number",
                    "--build-url",
                    "http://example.com/",
                    "--commit-id",
                    "101010",
                    "--commit-message",
                    "Hello",
                    "--smtp-host",
                    "mail.example.com",
                    "--smtp-user",
                    "user",
                    "--smtp-password",
                    "password",
                    "--email-sender",
                    "sender@example.com",
                    "--email-recipient",
                    "recipient@example.com",
                ],
            )

        self.assertEqual(result.exitCode, 1)
        self.assertEqual(result.stdout.getvalue(), "")

        errors = result.stderr.getvalue()

        self.assertTrue(
            errors.startswith(
                "Error: SSL failure while connecting to mail.example.com:465: "
            )
        )
