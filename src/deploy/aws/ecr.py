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
from typing import Any, ClassVar, Dict, Iterable, List, Optional

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
from docker.models.images import Image

from twisted.logger import Logger

from deploy.ext.click import readConfig
from deploy.ext.logger import startLogging


__all__ = (
    "InvalidImageNameError",
    "ECRAuthorizationToken",
    "ECRServiceClient",
)

Boto3ECRClient = Any


def utcNow() -> DateTime:
    return DateTime.utcnow().replace(tzinfo=TimeZone.utc)



class InvalidImageNameError(Exception):
    """
    Invalid Docker image name.
    """



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECRAuthorizationToken(object):
    """
    EC2 Container Registry Authorization Token
    """

    username: str
    password: str
    expiration: DateTime
    proxyEndpoint: str

    def credentials(self) -> Dict[str, str]:
        """
        Returns credentials as required by the auth_config argument to
        docker.client.images.push().
        """
        return {
            "username": self.username,
            "password": self.password,
        }



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECRServiceClient(object):
    """
    EC2 Container Registry Client
    """

    @staticmethod
    def validateImageName(name: str) -> None:
        """
        Validate an image name.
        Note: ECRServiceClient always requires image names to include the tag;
        ":latest" is not inferred if the tag is missing.
        """
        try:
            repository, tag = name.split(":")
        except ValueError:
            raise InvalidImageNameError(name)


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

            now = utcNow()

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


    # def login(self) -> None:
    #     """
    #     Log Docker into ECR.
    #     """
    #     token = self.authorizationToken()
    #     self.log.debug("Logging into ECR Docker registry...")
    #     response = self._docker.login(
    #         username=token.username,
    #         password=token.password,
    #         registry=token.proxyEndpoint,
    #         reauth=True,
    #     )
    #     idToken = response["IdentityToken"]
    #     status = response["Status"]
    #     assert status == "Login Succeeded"
    #     self.log.info("Logged into ECR Docker registry.", idToken=idToken)


    def listImages(self) -> Iterable[Image]:
        """
        List images.
        """
        return self._docker.images.list()


    def imageWithName(self, name: str) -> Image:
        """
        Look up the named image.
        """
        self.validateImageName(name)
        return self._docker.images.get(name)


    def tag(self, existingName: str, newName: str) -> None:
        """
        Tag an image.
        """
        image = self.imageWithName(existingName)

        try:
            repository, tag = newName.split(":")
        except ValueError:
            raise InvalidImageNameError(newName)

        image.tag(repository=repository, tag=tag)

        self.log.info(
            "Tagged image {image.short_id} ({existingName}) as {newName}.",
            image=image, existingName=existingName, newName=newName,
        )


    def push(self, localName: str, ecrName: str) -> None:
        """
        Tag a local image named localName with ecrName and push the image to
        ECR with the new tag.
        """
        self.validateImageName(localName)

        try:
            repository, tag = ecrName.split(":")
        except ValueError:
            raise InvalidImageNameError(ecrName)

        image = self._docker.images.get(localName)
        image.tag(repository, tag)

        credentials = self.authorizationToken().credentials()

        self.log.debug(
            "Pushing image {image.short_id} ({localName}) "
            "to ECR with name {ecrName}...",
            image=image, localName=localName, ecrName=ecrName,
        )
        self._docker.images.push(repository, tag, auth_config=credentials)
        self.log.info(
            "Pushed image {image.short_id} ({localName}) "
            "to ECR with name {ecrName}.",
            image=image, localName=localName, ecrName=ecrName,
        )



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
                "authorization",
                "list",
                "tag",
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


# @main.command()
# def login() -> None:
#     client = ECRServiceClient()
#     client.login()


@main.command(name="list")
def listImages() -> None:
    client = ECRServiceClient()
    images = client.listImages()
    for image in images:
        tags = ", ".join(t for t in image.tags)
        click.echo(f"{image.id}: {tags}")


@main.command()
@click.argument("existing-name")
@click.argument("new-name")
def tag(existing_name: str, new_name: str) -> None:
    client = ECRServiceClient()
    client.tag(existing_name, new_name)


@main.command()
@click.argument("local-name")
@click.argument("ecr-name")
def push(local_name: str, ecr_name: str) -> None:
    client = ECRServiceClient()
    client.push(local_name, ecr_name)
