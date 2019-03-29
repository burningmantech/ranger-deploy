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
from ssl import Purpose, create_default_context
from typing import Callable, Optional, Tuple, Union

from attr import attrs

from click import (
    BadParameter, Context as ClickContext, Option, Parameter,
    group as commandGroup, option as commandOption,
    pass_context as passContext, version_option as versionOption,
)

from twisted.logger import Logger

from deploy.ext.click import readConfig
from deploy.ext.logger import startLogging


__all__ = (
    "SMTPNotifier",
)



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class SMTPNotifier(object):
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
        project: str, repository: str, buildNumber: str, buildURL: str,
        commitID: str, commitMessage: str,
    ) -> None:
        """
        Send notification of a deployment to staging.
        """
        self.log.info(
            "Sending email notification for project {project} ({repository}) "
            "build {buildNumber} of commit {commitID}...",
            project=project, repository=repository,
            buildNumber=buildNumber, buildURL=buildURL,
            commitID=commitID, commitMessage=commitMessage,
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

        context = create_default_context(purpose=Purpose.CLIENT_AUTH)
        with SMTP_SSL(self.smtpHost, self.smtpPort, context=context) as relay:
            relay.login(self.smtpUser, self.smtpPassword)
            relay.send_message(
                message, self.senderAddress, self.recipientAddress
            )



#
# Command line
#

def validateRepositoryID(
    ctx: ClickContext, param: Union[Option, Parameter], value: str
) -> Tuple[str, str, str]:
    try:
        organization, project = value.split("/")
    except ValueError:
        raise BadParameter(f"Invalid repository ID: {value}")

    return (value, organization, project)


@commandGroup()
@versionOption()
@commandOption(
    "--profile",
    help="Profile to load from configuration file",
    type=str, metavar="<name>", prompt=False, required=False,
)
@passContext
def main(ctx: ClickContext, profile: Optional[str]) -> None:
    """
    SMTP notification tool.
    """
    if ctx.default_map is None:
        commonDefaults = readConfig(profile=profile)

        commonDefaults.setdefault(
            "cluster", commonDefaults.get("staging_cluster")
        )
        commonDefaults.setdefault(
            "service", commonDefaults.get("staging_service")
        )

        ctx.default_map = {
            command: commonDefaults for command in (
                "staging",
                "rollback",
                "production",
                "compare",
                "environment",
            )
        }

    startLogging()


@main.command()
@commandOption(
    "--project-name",
    envvar="PROJECT_NAME",
    help="project name",
    type=str, metavar="<name>",
    prompt=False, required=False,
)
@commandOption(
    "--repository-id",
    envvar="REPOSITORY_ID",
    help="repository",
    type=str, metavar="<organization>/<project>",
    prompt=True, required=True,
    callback=validateRepositoryID,
)
@commandOption(
    "--build-number",
    envvar="BUILD_NUMBER",
    help="build number",
    type=str, metavar="<number>",
    prompt=True, required=True,
)
@commandOption(
    "--build-url",
    envvar="BUILD_URL",
    help="build URL",
    type=str, metavar="<url>",
    prompt=True, required=True,
)
@commandOption(
    "--commit-id",
    envvar="COMMIT_ID",
    help="commit ID",
    type=str, metavar="<id>",
    prompt=True, required=True,
)
@commandOption(
    "--commit-message",
    envvar="COMMIT_MESSAGE",
    help="commit message",
    type=str, metavar="<message>",
    prompt=True, required=True,
)
@commandOption(
    "--smtp-host",
    envvar="NOTIFY_SMTP_HOST",
    help="SMTP server host name",
    type=str, metavar="<host>",
    prompt=True, required=True,
)
@commandOption(
    "--smtp-port",
    envvar="NOTIFY_SMTP_PORT",
    help="SMTP server port",
    type=int, metavar="<port>",
    prompt=False, required=False, default=465,
)
@commandOption(
    "--smtp-user",
    envvar="NOTIFY_SMTP_USER",
    help="SMTP user name",
    type=str, metavar="<user>",
    prompt=True, required=True,
)
@commandOption(
    "--smtp-password",
    envvar="NOTIFY_SMTP_PASSWORD",
    help="SMTP user password",
    type=str, metavar="<password>",
    prompt=True, required=True,
)
@commandOption(
    "--sender",
    envvar="NOTIFY_EMAIL_SENDER",
    help="email sender address",
    type=str, metavar="<address>",
    prompt=True, required=True,
)
@commandOption(
    "--recipient",
    envvar="NOTIFY_EMAIL_RECIPIENT",
    help="email recipient address",
    type=str, metavar="<address>",
    prompt=True, required=True,
)
def staging(
    project_name: Optional[str], repository_id: Tuple[str, str, str],
    build_number: str, build_url: str, commit_id: str, commit_message: str,
    smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
    sender: str, recipient: str,
) -> None:
    """
    Send an email notification of a deployment to the staging environment.
    """
    repository, organization, project = repository_id

    if project_name is None:
        project_name = project

    notifier = SMTPNotifier(
        smtpHost=smtp_host,
        smtpPort=smtp_port,
        smtpUser=smtp_user,
        smtpPassword=smtp_password,
        senderAddress=sender,
        recipientAddress=recipient,
    )

    notifier.notifyStaging(
        project=project_name, repository=repository,
        buildNumber=build_number, buildURL=build_url,
        commitID=commit_id, commitMessage=commit_message,
    )


if __name__ == "__main__":  # pragma: no cover
    SMTPNotifier.main()
