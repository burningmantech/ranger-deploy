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
AWS EC2 Container Registry support.
"""

from base64 import b64decode
from datetime import (
    datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
)
from ssl import (
    OP_NO_SSLv2, OP_NO_SSLv3, OP_NO_TLSv1, OP_NO_TLSv1_1, PROTOCOL_TLS
)
from typing import Any, ClassVar, List, Optional

from attr import Factory, attrs

from boto3 import client as boto3Client

import click
from click import (
    Context as ClickContext, group as commandGroup, option as commandOption,
    pass_context as passContext, version_option as versionOption,
)

from docker import (
    APIClient as DockerClient, from_env as dockerClientFromEnvironment
)

from twisted.logger import Logger

from deploy.ext.click import readConfig
from deploy.ext.logger import startLogging


__all__ = (
    "ECRAuthorizationToken",
    "ECRServiceClient",
)

Boto3ECRClient = Any



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECRAuthorizationToken(object):
    """
    EC2 Container Registry Authorization Token
    """

    username: str
    password: str
    expiration: DateTime
    proxyEndpoint: str



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECRServiceClient(object):
    """
    EC2 Container Registry Client
    """

    #
    # Class attributes
    #

    log = Logger()

    _tokenRefreshWindow: ClassVar = TimeDelta(minutes=30)
    _tlsVersion: ClassVar = (
        PROTOCOL_TLS
        | OP_NO_SSLv2 | OP_NO_SSLv3
        | OP_NO_TLSv1 | OP_NO_TLSv1_1
    )



    @classmethod
    def main(cls) -> None:
        """
        Command line entry point.
        """
        main()


    #
    # Instance attributes
    #

    _botoClient: List[Boto3ECRClient] = Factory(list)
    _dockerClient: List[DockerClient] = Factory(list)
    _authorizationToken: List[ECRAuthorizationToken] = Factory(list)


    @property
    def _aws(self) -> Boto3ECRClient:
        if not self._botoClient:
            self._botoClient.append(boto3Client("ecr"))
        return self._botoClient[0]


    @property
    def _docker(self) -> DockerClient:
        if not self._dockerClient:
            self._dockerClient.append(dockerClientFromEnvironment(
                ssl_version=self._tlsVersion,
            ))
        return self._dockerClient[0]


    def authorizationToken(self) -> ECRAuthorizationToken:
        """
        Obtain an authorization token for the registry.
        """
        if self._authorizationToken:
            expiration = self._authorizationToken[0].expiration

            now = DateTime.utcnow().replace(tzinfo=TimeZone.utc)

            # Subtract refresh window from expiration time so we aren't using a
            # token that's nearly expired.
            if now >= expiration - self._tokenRefreshWindow:
                self._authorizationToken.clear()

        if not self._authorizationToken:
            self.log.debug("Obtaining ECR authorization...")

            response = self._aws.get_authorization_token()
            assert len(response["authorizationData"]) == 1

            data = response["authorizationData"][0]
            token = data["authorizationToken"]
            username, password = b64decode(token).decode("utf-8").split(':')

            self._authorizationToken.append(
                ECRAuthorizationToken(
                    username=username, password=password,
                    expiration=data["expiresAt"],
                    proxyEndpoint=data["proxyEndpoint"],
                )
            )

            self.log.info(
                "Obtained ECR authorization as user {user}", user=username
            )

        return self._authorizationToken[0]


    def login(self) -> None:
        """
        Log Docker into ECR,
        """
        token = self.authorizationToken()
        self.log.debug("Logging into ECR Docker registry...")
        response = self._docker.login(
            username=token.username,
            password=token.password,
            registry=token.proxyEndpoint,
            reauth=True,
        )
        idToken = response["IdentityToken"]
        status = response["Status"]
        assert status == "Login Succeeded"
        self.log.info("Logged into ECR Docker registry.", idToken=idToken)



#
# Command line
#

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
    AWS Elastic Container Service deployment tool.
    """
    if ctx.default_map is None:
        commonDefaults = readConfig(profile=profile)

        ctx.default_map = {
            command: commonDefaults for command in (
            )
        }

    startLogging()


@main.command()
def authorization() -> None:
    """
    Print authorization information for the registry.
    """
    client = ECRServiceClient()
    token = client.authorizationToken()
    click.echo(f"User: {token.username}")
    click.echo(f"Password: {token.password}")
    click.echo(f"Proxy endpoint: {token.proxyEndpoint}")
    click.echo(f"Expiration: {token.expiration}")


@main.command()
def login() -> None:
    client = ECRServiceClient()
    client.login()
