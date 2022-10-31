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
Support for sending notifications via SMTP.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as escapeHTML
from pkgutil import get_data as readResource
from smtplib import SMTP_SSL
from ssl import Purpose, SSLError, create_default_context
from typing import Callable, Optional, Tuple, Union

from attr import attrs
from click import BadParameter, ClickException
from click import Context as ClickContext
from click import Option, Parameter
from click import group as commandGroup
from click import option as commandOption
from click import pass_context as passContext
from click import version_option as versionOption
from twisted.logger import Logger

from deploy.ext.click import (
    composedOptions,
    profileOption,
    readConfig,
    trialRunOption,
)
from deploy.ext.logger import startLogging

from ._error import FailedToSendNotificationError


__all__ = ("SMTPNotifier",)


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class SMTPNotifier:
    """
    SMTP Notifier
    """

    #
    # Class attributes
    #

    log = Logger()

    @classmethod
    def main(cls) -> None:
        """
        Command line entry point.
        """
        main()

    #
    # Instance attributes
    #

    smtpHost: str
    smtpPort: int
    smtpUser: str
    smtpPassword: str
    senderAddress: str
    recipientAddress: str

    def notifyStaging(
        self,
        project: str,
        repository: str,
        buildNumber: str,
        buildURL: str,
        commitID: str,
        commitMessage: str,
        trialRun: bool,
    ) -> None:
        """
        Send notification of a deployment to staging.
        """
        self.log.debug(
            "Sending email notification for project {project} ({repository}) "
            "build {buildNumber} of commit {commitID}...",
            project=project,
            repository=repository,
            buildNumber=buildNumber,
            buildURL=buildURL,
            commitID=commitID,
            commitMessage=commitMessage,
        )

        title = f"{project} Deployed to Staging"

        message = MIMEMultipart("alternative")
        message["Subject"] = title
        message["From"] = self.senderAddress
        message["To"] = self.recipientAddress

        def formatTemplate(
            name: str, escape: Callable[[str], str] = lambda s: s
        ) -> str:
            formatSpec = dict(
                buildNumber=buildNumber,
                buildURL=buildURL,
                commitID=commitID,
                commitIDShort=commitID[:7],
                commitMessage=commitMessage,
                commitURL=f"https://github.com/{repository}/commit/{commitID}",
                project=project,
                repository=repository,
                title=title,
            )

            text = readResource("deploy.notify", f"templates/{name}")
            assert text is not None
            return text.decode("utf-8").format(**formatSpec)

        text = formatTemplate("message.txt")
        html = formatTemplate("message.html", escape=escapeHTML)

        message.attach(MIMEText(text, "plain"))
        message.attach(MIMEText(html, "html"))

        if not trialRun:
            context = create_default_context(purpose=Purpose.CLIENT_AUTH)
            try:
                with SMTP_SSL(
                    self.smtpHost, self.smtpPort, context=context
                ) as relay:
                    relay.login(self.smtpUser, self.smtpPassword)
                    relay.send_message(
                        message, self.senderAddress, self.recipientAddress
                    )
            except SSLError as e:
                raise FailedToSendNotificationError(
                    "SSL failure while connecting to "
                    f"{self.smtpHost}:{self.smtpPort}"
                ) from e

        self.log.info(
            "Sent email notification for project {project} ({repository}) "
            "build {buildNumber} of commit {commitID}...",
            project=project,
            repository=repository,
            buildNumber=buildNumber,
            buildURL=buildURL,
            commitID=commitID,
            commitMessage=commitMessage,
        )


#
# Command line
#


def validateRepositoryID(
    ctx: ClickContext, param: Union[Option, Parameter], value: Optional[str]
) -> Optional[Tuple[str, str, str]]:
    if value is None:
        return None

    try:
        organization, project = value.split("/")
    except ValueError:
        raise BadParameter(f"Invalid repository ID: {value}") from None

    return (value, organization, project)


def buildOptions(required: bool = True) -> Callable[..., Callable]:
    return composedOptions(
        commandOption(
            "--project-name",
            envvar="PROJECT_NAME",
            help="project name",
            type=str,
            metavar="<name>",
            prompt=False,
            required=False,
        ),
        commandOption(
            "--repository-id",
            envvar="REPOSITORY_ID",
            help="repository",
            type=str,
            metavar="<organization>/<project>",
            prompt=required,
            required=required,
            callback=validateRepositoryID,
        ),
        commandOption(
            "--build-number",
            envvar="BUILD_NUMBER",
            help="build number",
            type=str,
            metavar="<number>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--build-url",
            envvar="BUILD_URL",
            help="build URL",
            type=str,
            metavar="<url>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--commit-id",
            envvar="COMMIT_ID",
            help="commit ID",
            type=str,
            metavar="<id>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--commit-message",
            envvar="COMMIT_MESSAGE",
            help="commit message",
            type=str,
            metavar="<message>",
            prompt=required,
            required=required,
        ),
    )


def smtpOptions(required: bool = True) -> Callable[..., Callable]:
    return composedOptions(
        commandOption(
            "--smtp-host",
            envvar="NOTIFY_SMTP_HOST",
            help="SMTP server host name",
            type=str,
            metavar="<host>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--smtp-port",
            envvar="NOTIFY_SMTP_PORT",
            help="SMTP server port",
            type=int,
            metavar="<port>",
            prompt=False,
            required=False,
            default=465,
        ),
        commandOption(
            "--smtp-user",
            envvar="NOTIFY_SMTP_USER",
            help="SMTP user name",
            type=str,
            metavar="<user>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--smtp-password",
            envvar="NOTIFY_SMTP_PASSWORD",
            help="SMTP user password",
            type=str,
            metavar="<password>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--email-sender",
            envvar="NOTIFY_EMAIL_SENDER",
            help="email sender address",
            type=str,
            metavar="<address>",
            prompt=required,
            required=required,
        ),
        commandOption(
            "--email-recipient",
            envvar="NOTIFY_EMAIL_RECIPIENT",
            help="email recipient address",
            type=str,
            metavar="<address>",
            prompt=required,
            required=required,
        ),
    )


@commandGroup()
@versionOption()
@profileOption
@passContext
def main(ctx: ClickContext, profile: Optional[str]) -> None:
    """
    SMTP notification tool.
    """
    if ctx.default_map is None:
        commonDefaults = readConfig(profile=profile)

        ctx.default_map = {command: commonDefaults for command in ("staging",)}

    startLogging()


@main.command()
@buildOptions()
@smtpOptions()
@trialRunOption
def staging(
    project_name: Optional[str],
    repository_id: Optional[Tuple[str, str, str]],
    build_number: str,
    build_url: str,
    commit_id: str,
    commit_message: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_sender: str,
    email_recipient: str,
    trial_run: bool,
) -> None:
    """
    Send an email notification of a deployment to staging.
    """
    assert repository_id is not None

    _staging(
        project_name=project_name,
        repository_id=repository_id,
        build_number=build_number,
        build_url=build_url,
        commit_id=commit_id,
        commit_message=commit_message,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        email_sender=email_sender,
        email_recipient=email_recipient,
        trial_run=trial_run,
    )


def _staging(
    project_name: Optional[str],
    repository_id: Tuple[str, str, str],
    build_number: str,
    build_url: str,
    commit_id: str,
    commit_message: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_sender: str,
    email_recipient: str,
    trial_run: bool,
) -> None:
    """
    Send an email notification of a deployment to staging.
    """
    repository, organization, project = repository_id

    if project_name is None:
        project_name = project

    notifier = SMTPNotifier(
        smtpHost=smtp_host,
        smtpPort=smtp_port,
        smtpUser=smtp_user,
        smtpPassword=smtp_password,
        senderAddress=email_sender,
        recipientAddress=email_recipient,
    )

    try:
        notifier.notifyStaging(
            project=project_name,
            repository=repository,
            buildNumber=build_number,
            buildURL=build_url,
            commitID=commit_id,
            commitMessage=commit_message,
            trialRun=trial_run,
        )
    except FailedToSendNotificationError as e:
        raise ClickException(e.message) from e


if __name__ == "__main__":  # pragma: no cover
    SMTPNotifier.main()
