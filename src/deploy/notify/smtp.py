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
from ssl import create_default_context as SSLContext
from typing import Callable

from attr import attrs


__all__ = (
    "SMTPNotifier",
)



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class SMTPNotifier(object):
    """
    SMTP Notifier
    """

    smtpHost: str
    smtpPort: int
    smtpUser: str
    smtpPassword: str
    senderAddress: str
    destinationAddress: str


    def notifyStaging(
        self,
        project: str, repository: str, buildNumber: str, buildURL: str,
        commitID: str, commitMessage: str,
    ) -> None:
        """
        Send notification of a deployment to staging.
        """
        title = f"{project} Deployed to Staging"

        message = MIMEMultipart("alternative")
        message["Subject"] = title
        message["From"] = self.senderAddress
        message["To"] = self.destinationAddress

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

        context = SSLContext()
        with SMTP_SSL(self.smtpHost, self.smtpPort, context=context) as relay:
            relay.login(self.smtpUser, self.smtpPassword)
            relay.send_message(
                message, self.senderAddress, self.destinationAddress,
            )
