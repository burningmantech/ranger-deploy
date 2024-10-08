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
from collections.abc import Iterable
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from datetime import timezone as TimeZone
from enum import IntEnum
from ssl import (
    PROTOCOL_TLS,
    OP_NO_SSLv2,
    OP_NO_SSLv3,
    OP_NO_TLSv1,
    OP_NO_TLSv1_1,
)
from typing import Any, ClassVar, Optional, Union, cast

import click
from attrs import Factory, frozen, mutable
from boto3 import client as boto3Client
from click import Context as ClickContext
from click import group as commandGroup
from click import pass_context as passContext
from click import version_option as versionOption
from docker import APIClient as DockerClient
from docker import from_env as dockerClientFromEnvironment
from docker.models.images import Image
from twisted.logger import Logger

from deploy.ext.click import profileOption, readConfig
from deploy.ext.json import objectFromJSONText
from deploy.ext.logger import startLogging


__all__ = (
    "InvalidImageNameError",
    "ECRAuthorizationToken",
    "ECRServiceClient",
)

Boto3ECRClient = Any


def utcNow() -> DateTime:
    return DateTime.utcnow().replace(tzinfo=TimeZone.utc)


@mutable
class DockerServiceError(Exception):
    """
    Error from Docker service.
    """

    message: str


@mutable
class InvalidImageNameError(Exception):
    """
    Invalid Docker image name.
    """

    name: str


@frozen(kw_only=True)
class ECRAuthorizationToken:
    """
    EC2 Container Registry Authorization Token
    """

    username: str
    password: str
    expiration: DateTime
    proxyEndpoint: str

    def credentials(self) -> dict[str, str]:
        """
        Returns credentials as required by the auth_config argument to
        docker.client.images.push().
        """
        return {
            "username": self.username,
            "password": self.password,
        }


@frozen(kw_only=True)
class ECRServiceClient:
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
            raise InvalidImageNameError(name) from None

    #
    # Class attributes
    #

    log = Logger()

    _tokenRefreshWindow: ClassVar = TimeDelta(minutes=30)
    _tlsVersion: ClassVar = (
        PROTOCOL_TLS | OP_NO_SSLv2 | OP_NO_SSLv3 | OP_NO_TLSv1 | OP_NO_TLSv1_1
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

    _botoClient: list[Boto3ECRClient] = Factory(list)
    _dockerClient: list[DockerClient] = Factory(list)
    _authorizationToken: list[ECRAuthorizationToken] = Factory(list)

    @property
    def _aws(self) -> Boto3ECRClient:
        if not self._botoClient:
            self._botoClient.append(boto3Client("ecr"))
        return self._botoClient[0]

    @property
    def _docker(self) -> DockerClient:
        if not self._dockerClient:
            self._dockerClient.append(
                dockerClientFromEnvironment(
                    ssl_version=self._tlsVersion,
                )
            )
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

            try:
                response = self._aws.get_authorization_token()
            except Exception:
                self.log.critical("Unable to obtain authorization token")
                raise
            assert len(response["authorizationData"]) == 1

            data = response["authorizationData"][0]
            token = data["authorizationToken"]
            username, password = b64decode(token).decode("utf-8").split(":")

            self._authorizationToken.append(
                ECRAuthorizationToken(
                    username=username,
                    password=password,
                    expiration=data["expiresAt"],
                    proxyEndpoint=data["proxyEndpoint"],
                )
            )

            self.log.info(
                "Obtained ECR authorization as user {user}", user=username
            )

        return self._authorizationToken[0]

    def listImages(self) -> Iterable[Image]:
        """
        List images.
        """
        return cast(Iterable[Image], self._docker.images.list())

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
            raise InvalidImageNameError(newName) from None

        image.tag(repository=repository, tag=tag)

        self.log.info(
            "Tagged image {image.short_id} ({existingName}) as {newName}.",
            image=image,
            existingName=existingName,
            newName=newName,
        )

    def push(
        self, localName: str, ecrName: str, trialRun: bool = False
    ) -> None:
        """
        Tag a local image named localName with ecrName and push the image to
        ECR with the new tag.
        """
        try:
            repository, tag = ecrName.split(":")
        except ValueError:
            raise InvalidImageNameError(ecrName) from None

        self.tag(localName, ecrName)

        credentials = self.authorizationToken().credentials()

        self.log.debug(
            "Pushing image {localName} to ECR with name {ecrName}...",
            localName=localName,
            ecrName=ecrName,
        )
        if not trialRun:
            response = self._docker.images.push(
                repository, tag, auth_config=credentials, stream=True
            )
            handler = DockerPushResponseHandler(repository=repository, tag=tag)
            handler.handleResponse(response=response)
            for error in handler.errors:
                self.log.error(
                    "Error processing response while pushing to "
                    "{imageName}: {error}",
                    imageName=ecrName,
                    error=error,
                )
        self.log.info(
            "Pushed image {localName} to ECR with name {ecrName}.",
            localName=localName,
            ecrName=ecrName,
        )


class ImagePushState(IntEnum):
    start = 1
    preparing = 2
    waiting = 3
    pushing = 4
    pushed = 5


@frozen(kw_only=True)
class ImagePushStatus:
    state: ImagePushState = ImagePushState.start

    currentProgress: int = 0
    totalProgress: int = -1


@frozen(kw_only=True)
class ImagePushResult:
    tag: str
    digest: str
    size: int


@frozen(kw_only=True)
class DockerPushResponseHandler:
    log = Logger()

    _repoStatusPrefix = "The push refers to repository ["
    _repoStatusSuffix = "]"

    repository: str
    tag: str

    status: dict[str, ImagePushStatus] = Factory(dict)
    errors: list[str] = Factory(list)
    result: list[ImagePushResult] = Factory(list)

    def _statusForImage(self, imageID: str) -> ImagePushStatus:
        return self.status.setdefault(imageID, ImagePushStatus())

    def _error(self, message: str) -> None:
        self.log.error("Docker push error: {error}", error=message)
        self.errors.append(message)

    def _handleGeneralStatusUpdate(self, json: dict[str, Any]) -> None:
        message = json["status"]

        if message.startswith(self._repoStatusPrefix):
            assert message.endswith(self._repoStatusSuffix)

            repository = message[
                len(self._repoStatusPrefix) : -len(self._repoStatusSuffix)
            ]
            assert (
                repository == self.repository
            ), f"{repository} != {self.repository}"

        elif (
            message.startswith(f"{self.tag}: digest: ") and "size: " in message
        ):
            pass

        else:
            self._error(f"Unknown push status message: {message!r}")

    def _handleImageStatusUpdate(self, json: dict[str, Any]) -> None:
        assert not self.result

        imageID = json["id"]
        priorStatus = self._statusForImage(imageID)

        jsonStatus = json["status"]
        try:
            state = ImagePushState[jsonStatus.lower()]
        except KeyError:
            if jsonStatus == "Layer already exists":
                state = ImagePushState.pushed
                currentProgress = 0
                totalProgress = -1
            else:
                raise DockerServiceError(
                    f"Unknown status: {jsonStatus}"
                ) from None
        else:
            assert state != ImagePushState.start
            assert priorStatus.state <= state

            if (
                state is ImagePushState.preparing
                or state is ImagePushState.waiting
            ):
                assert (
                    priorStatus.currentProgress == 0
                    and priorStatus.totalProgress == -1
                ), priorStatus

                currentProgress = priorStatus.currentProgress
                totalProgress = priorStatus.totalProgress

            elif state is ImagePushState.pushing:
                progressDetail = json["progressDetail"]

                currentProgress = progressDetail["current"]
                totalProgress = progressDetail.get("total")

                assert currentProgress >= priorStatus.currentProgress
                if totalProgress is None:
                    assert (  # type: ignore[unreachable]
                        priorStatus.totalProgress == -1
                    )
                    totalProgress = -1
                else:
                    assert (
                        totalProgress == priorStatus.totalProgress
                        or priorStatus.totalProgress == -1
                    )

            elif state is ImagePushState.pushed:
                assert priorStatus.state < state

                totalProgress = priorStatus.totalProgress

                currentProgress = totalProgress

        self.status[imageID] = ImagePushStatus(
            state=state,
            currentProgress=currentProgress,
            totalProgress=totalProgress,
        )

    def _handleAux(self, json: dict[str, Any]) -> None:
        assert not self.result

        aux = json["aux"]

        self.result.append(
            ImagePushResult(
                tag=aux["Tag"], digest=aux["Digest"], size=aux["Size"]
            )
        )

    def _handleLine(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        self.log.debug("Docker push response line: {line}", line=line)

        json = objectFromJSONText(line)

        if isinstance(json, dict):
            if "errorDetail" in json:
                raise DockerServiceError(json["errorDetail"])

            if "status" in json:
                if "id" in json:
                    self._handleImageStatusUpdate(json)
                else:
                    self._handleGeneralStatusUpdate(json)
                return

            elif "aux" in json:
                self._handleAux(json)
                return

        raise DockerServiceError(f"Unrecognized push response JSON: {json}")

    def _handlePayload(self, payload: bytes) -> None:
        assert isinstance(payload, bytes)

        for line in payload.decode("utf-8").split("\n"):
            try:
                self._handleLine(line)
            except Exception as e:
                from twisted.python.failure import Failure

                self.log.critical(
                    "While handling push response line: {line}",
                    line=line,
                    failure=Failure(),
                )
                self._error(str(e))

    def handleResponse(
        self,
        response: Union[bytes, Iterable[bytes]],
    ) -> None:
        if isinstance(response, bytes):
            self._handlePayload(response)
            return

        for payload in response:
            self._handlePayload(payload)


#
# Command line
#


@commandGroup()
@versionOption()
@profileOption
@passContext
def main(ctx: ClickContext, profile: Optional[str]) -> None:
    """
    AWS Elastic Container Service deployment tool.
    """
    if ctx.default_map is None:
        commonDefaults = readConfig(profile=profile)

        ctx.default_map = {
            command: commonDefaults
            for command in (
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


if __name__ == "__main__":  # pragma: no cover
    ECRServiceClient.main()
