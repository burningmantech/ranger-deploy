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
Tests for :mod:`deploy.aws.ecr`
"""

from base64 import b64encode
from contextlib import contextmanager
from datetime import timedelta as TimeDelta
from hashlib import sha256
from json import JSONDecodeError
from ssl import Options as SSLOptions
from typing import (
    Any,
    ClassVar,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Type,
    Union,
    cast,
)

from attr import Attribute, Factory, attrib, attrs
from docker.errors import ImageNotFound
from hypothesis import assume, given
from hypothesis.strategies import integers, lists, text
from twisted.trial.unittest import SynchronousTestCase as TestCase

from deploy.ext.click import ClickTestResult, clickTestRun
from deploy.ext.json import jsonTextFromObject
from deploy.ext.logger import logCapture

from .. import ecr
from ..ecr import (
    DockerPushResponseHandler,
    DockerServiceError,
    ECRAuthorizationToken,
    ECRServiceClient,
    ImagePushResult,
    ImagePushState,
    ImagePushStatus,
    InvalidImageNameError,
    utcNow,
)


__all__ = ()


@attrs(auto_attribs=True)
class MockBoto3ECRClient:
    """
    Mock Boto3 client.
    """

    #
    # Class attributes
    #

    _defaultRepositoryID: ClassVar[str] = "101010101010"

    @classmethod
    def _setUp(cls) -> None:
        pass

    @classmethod
    def _tearDown(cls) -> None:
        pass

    #
    # Instance attributes
    #

    _awsService: str = attrib()

    @_awsService.validator
    def _validate_service(self, attribute: Attribute, value: Any) -> None:
        assert value == "ecr"

    def get_authorization_token(
        self, registryIds: Sequence[str] = ()
    ) -> Dict[str, List[Dict[str, Any]]]:
        assert not registryIds

        token = b64encode(b"AWS:see-kreht")
        expiration = utcNow() + TimeDelta(hours=12)
        proxyEndpoint = f"https://{self._defaultRepositoryID}.ecr.aws"

        return {
            "authorizationData": [
                {
                    "authorizationToken": token,
                    "expiresAt": expiration,
                    "proxyEndpoint": proxyEndpoint,
                },
            ],
        }


@attrs(auto_attribs=True)
class MockImage:
    id: str
    tags: List[str]

    def tag(self, repository: str, tag: Optional[str] = None) -> bool:
        assert ":" not in repository
        assert tag is not None
        name = f"{repository}:{tag}"
        self.tags.append(name)
        return True


@attrs(auto_attribs=True)
class MockImagesAPI:
    _fakeNewsError = "This error is fake news."

    _parent: "MockDockerClient"

    def list(self) -> List[MockImage]:
        return self._parent._localImages

    def get(self, name: str) -> MockImage:
        assert ":" in name
        for image in self.list():
            if name in image.tags:
                return image

        raise ImageNotFound(name)

    def _fromECR(self, name: str) -> Optional[MockImage]:
        images: List[MockImage] = []
        for image in self._parent._cloudImages:
            if name in image.tags:
                images.append(image)

        if not images:
            return None

        assert len(images) == 1

        return images[0]

    def push(
        self,
        repository: str,
        tag: Optional[str] = None,
        stream: bool = False,
        decode: bool = False,
        auth_config: Optional[Dict[str, str]] = None,
    ) -> Union[bytes, Iterator[bytes]]:
        assert ":" not in repository
        assert tag is not None
        assert stream is True

        assert not decode, "decode not implemented"

        name = f"{repository}:{tag}"

        image = self.get(name)
        ecrImage = MockImage(id=image.id, tags=[name])

        # Untag any existing images with the same name
        existing = self._fromECR(name)
        # No tests currently exercise replace a tag
        assert existing is None, "Replace with below when we hit this"
        # if existing is not None:
        #     existing.tags.remove(name)

        self._parent._cloudImages.append(ecrImage)

        size = len(image.id)
        digest = f"sha256:{sha256Hash(image.id)}"

        # Fake some activity
        json = [
            {"status": f"The push refers to a repository [{repository}]"},
            {"status": "Preparing", "id": image.id, "progressDetail": {}},
            {"status": "Waiting", "id": image.id, "progressDetail": {}},
            {
                "status": "Pushing",
                "id": image.id,
                "progressDetail": {"current": 0, "total": size},
            },
            {
                "status": "Pushing",
                "id": image.id,
                "progressDetail": {"current": int(size / 2), "total": size},
            },
            {
                "status": "Pushing",
                "id": image.id,
                "progressDetail": {"current": size, "total": size},
            },
            {"status": "Pushed", "id": image.id, "progressDetail": {}},
            {"status": f"{tag}: digest: {digest} size: {size}"},
            {
                "aux": {"Tag": tag, "Digest": digest, "Size": size},
                "progressDetail": {},
            },
        ]

        if self._parent._generateErrors:
            json += [{"errorDetail": self._fakeNewsError}]

        return (jsonTextFromObject(j).encode("utf-8") for j in json)


def sha256Hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@attrs(auto_attribs=True)
class MockDockerClient:
    """
    Mock Docker client.
    """

    #
    # Class attributes
    #

    _localImages: ClassVar[List[MockImage]] = []
    _cloudImages: ClassVar[List[MockImage]] = []

    @classmethod
    def _defaultLocalImages(cls) -> List[MockImage]:
        return [
            MockImage(id=sha256Hash(name), tags=[name])
            for name in ("image:1", "image:2", "image:3")
        ]

    @classmethod
    def _defaultCloudImages(cls) -> List[MockImage]:
        return [
            MockImage(id=sha256Hash(name), tags=[name])
            for name in ("cloud:1", "cloud:2")
        ]

    @classmethod
    def _fromEnvironment(
        cls, ssl_version: Optional[SSLOptions] = None
    ) -> "MockDockerClient":
        return cls(sslVersion=ssl_version)

    @classmethod
    def _setUp(cls) -> None:
        # Copy images so that we aren't sharing mutable tags
        cls._localImages.extend(cls._defaultLocalImages())
        cls._cloudImages.extend(cls._defaultCloudImages())

    @classmethod
    def _tearDown(cls) -> None:
        cls._localImages.clear()
        cls._cloudImages.clear()

    #
    # Instance attributes
    #

    _sslVersion: Optional[SSLOptions]
    _generateErrors = False

    images: MockImagesAPI = Factory(MockImagesAPI, takes_self=True)


@contextmanager
def testingBoto3ECR() -> Iterator[None]:
    MockBoto3ECRClient._setUp()

    boto3Client = ecr.boto3Client
    ecr.boto3Client = MockBoto3ECRClient

    try:
        yield

    finally:
        ecr.boto3Client = boto3Client

        MockBoto3ECRClient._tearDown()


@contextmanager
def testingDocker() -> Iterator[None]:
    MockDockerClient._setUp()

    dockerClientFromEnvironment = ecr.dockerClientFromEnvironment
    ecr.dockerClientFromEnvironment = MockDockerClient._fromEnvironment

    try:
        yield

    finally:
        ecr.dockerClientFromEnvironment = dockerClientFromEnvironment

        MockDockerClient._tearDown()


class ECRAuthorizationTokenTests(TestCase):
    """
    Tests for :class:`ECRAuthorizationToken`
    """

    def test_credentials(self) -> None:
        token = ECRAuthorizationToken(
            username="user",
            password="password",
            expiration=utcNow(),
            proxyEndpoint="https://foo.example.com/ecr",
        )
        self.assertEqual(
            token.credentials(),
            dict(username=token.username, password=token.password),
        )


class ECRServiceClientTests(TestCase):
    """
    Tests for :class:`ECRServiceClient`
    """

    def test_aws(self) -> None:
        """
        :meth:`ECSServiceClient._aws` property returns an AWS client.
        """
        with testingBoto3ECR():
            client = ECRServiceClient()
            self.assertIsInstance(client._aws, MockBoto3ECRClient)

    def test_docker(self) -> None:
        """
        :meth:`ECSServiceClient._docker` property returns a Docker client.
        """
        with testingDocker():
            client = ECRServiceClient()
            self.assertIsInstance(client._docker, MockDockerClient)
            self.assertEqual(client._tlsVersion, ECRServiceClient._tlsVersion)

    def test_authorizationToken_new(self) -> None:
        with testingBoto3ECR():
            client = ECRServiceClient()
            token = client.authorizationToken()

            self.assertEqual(token.username, "AWS")
            self.assertEqual(token.password, "see-kreht")
            self.assertGreater(token.expiration, utcNow())
            self.assertEqual(
                token.proxyEndpoint,
                f"https://{MockBoto3ECRClient._defaultRepositoryID}.ecr.aws",
            )

    def test_authorizationToken_cached(self) -> None:
        with testingBoto3ECR():
            client = ECRServiceClient()
            token1 = client.authorizationToken()
            token2 = client.authorizationToken()

            self.assertIdentical(token1, token2)

    def test_authorizationToken_expired(self) -> None:
        with testingBoto3ECR():
            client = ECRServiceClient()
            token = client.authorizationToken()

            now = utcNow()
            username = token.username
            password = token.password
            proxyEndpoint = token.proxyEndpoint

            # A token expiring right now should not be vended a moment later.
            # A token expiring ahead but in the token refresh window should
            # also be replaced.
            for expiration in (now, now + client._tokenRefreshWindow):
                token = ECRAuthorizationToken(
                    username=username,
                    password=password,
                    expiration=expiration,
                    proxyEndpoint=proxyEndpoint,
                )
                client._authorizationToken[0] = token
                newToken = client.authorizationToken()

                self.assertNotEqual(newToken, token)

    def test_listImages(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            images = client.listImages()

            self.assertEqual(images, MockDockerClient._localImages)

    def test_imageWithName(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            image = client.imageWithName("image:1")
            self.assertIn("image:1", image.tags)

    def test_imageWithName_invalid(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "image"
            e = self.assertRaises(
                InvalidImageNameError, client.imageWithName, name
            )
            self.assertEqual(str(e), name)

    def test_imageWithName_notFound(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "xyzzy:fnord"
            e = self.assertRaises(ImageNotFound, client.imageWithName, name)
            self.assertEqual(str(e), name)

    def test_tag(self) -> None:
        with testingBoto3ECR(), testingDocker():
            existingName = "image:1"
            newName = "test:latest"

            client = ECRServiceClient()
            image = client.imageWithName(existingName)
            assert image.tags == [existingName]

            client.tag(existingName, newName)
            image = client.imageWithName(existingName)
            self.assertIn(newName, image.tags)

    def test_imageWithName_invalidExisting(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "image"
            e = self.assertRaises(
                InvalidImageNameError, client.tag, name, "test:latest"
            )
            self.assertEqual(str(e), name)

    def test_tag_invalidNew(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "test"
            e = self.assertRaises(
                InvalidImageNameError, client.tag, "image:1", name
            )
            self.assertEqual(str(e), name)

    def test_tag_doesntExist(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "xyzzy:fnord"
            e = self.assertRaises(
                ImageNotFound, client.tag, name, "test:latest"
            )
            self.assertEqual(str(e), name)

    def test_push(self) -> None:
        with testingBoto3ECR(), testingDocker():
            localTag = "image:1"
            ecrName = "test:latest"

            client = ECRServiceClient()
            client.push(localTag, ecrName)

            image = client._docker.images._fromECR(ecrName)

            self.assertIsNotNone(image, client._docker._cloudImages)
            self.assertIn(ecrName, image.tags)
            self.assertNotIn(localTag, image.tags)

    def test_push_error(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            client._docker._generateErrors = True

            with logCapture() as events:
                client.push("image:1", "test:latest")

                failureEvents = [
                    event for event in events if "failure" in event
                ]

            self.assertEqual(len(failureEvents), 1)

            failureEvent: Dict[str, Any] = failureEvents[0]

            fakeNewsError = MockImagesAPI._fakeNewsError

            self.assertEqual(
                failureEvent["log_format"],
                "While handling push response line: {line}",
            )
            self.assertEqual(
                failureEvent["line"],
                f'{{"errorDetail":"{fakeNewsError}"}}',
            )

            handler: DockerPushResponseHandler = failureEvent["log_source"]

            self.assertEqual(len(handler.errors), 1)
            self.assertEqual(handler.errors[0], fakeNewsError)

        self.flushLoggedErrors()

    def test_push_invalidLocal(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "image"
            e = self.assertRaises(
                InvalidImageNameError, client.push, name, "test:latest"
            )
            self.assertEqual(str(e), name)

    def test_push_invalidECR(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "test"
            e = self.assertRaises(
                InvalidImageNameError, client.push, "image:1", name
            )
            self.assertEqual(str(e), name)

    def test_push_doesntExist(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            name = "xyzzy:fnord"
            e = self.assertRaises(
                ImageNotFound, client.push, name, "test:latest"
            )
            self.assertEqual(str(e), name)


class DockerPushResponseHandlerTests(TestCase):
    """
    Tests for :class:`DockerPushResponseHandler`.
    """

    def test_statusForImage(self) -> None:
        handler = DockerPushResponseHandler(repository="repo", tag="tag")
        imageID = "1"
        self.assertEqual(handler._statusForImage(imageID), ImagePushStatus())

    @given(lists(text()))
    def test_error(self, messages: List[str]) -> None:
        handler = DockerPushResponseHandler(repository="repo", tag="tag")
        for message in messages:
            handler._error(message)
        self.assertEqual(handler.errors, messages)

    @given(text())
    def test_handleGeneralStatusUpdate_init(self, repository: str) -> None:
        handler = DockerPushResponseHandler(repository=repository, tag="tag")
        handler._handleGeneralStatusUpdate(
            json={"status": f"The push refers to a repository [{repository}]"}
        )
        self.assertEqual(handler.errors, [])

    @given(text(), text())
    def test_handleGeneralStatusUpdate_digest(
        self, tag: str, blob: str
    ) -> None:
        digest = sha256Hash(blob)
        size = len(blob)

        handler = DockerPushResponseHandler(repository="repo", tag=tag)
        handler._handleGeneralStatusUpdate(
            json={"status": f"{tag}: digest: sha256:{digest} size: {size}"}
        )
        self.assertEqual(handler.errors, [])

    @given(text(), text())
    def test_handleGeneralStatusUpdate_rando(
        self, status: str, tag: str
    ) -> None:
        assume(
            not status.startswith(DockerPushResponseHandler._repoStatusPrefix)
            or not status.endswith("]")
        )
        assume(not status.startswith(f"{tag}: "))

        handler = DockerPushResponseHandler(repository="repo", tag=tag)
        handler._handleGeneralStatusUpdate(json={"status": status})
        self.assertEqual(
            handler.errors, [f"Unknown push status message: {status!r}"]
        )

    @given(text())
    def test_handleImageStatusUpdate_exists(self, imageID: str) -> None:
        handler = DockerPushResponseHandler(repository="repo", tag="latest")
        handler._handleImageStatusUpdate(
            json={"status": "Layer already exists", "id": imageID}
        )
        self.assertEqual(
            handler._statusForImage(imageID),
            ImagePushStatus(
                state=ImagePushState.pushed,
                currentProgress=0,
                totalProgress=-1,
            ),
        )

    @given(text(min_size=1))
    def test_handleImageStatusUpdate_rando(self, status: str) -> None:
        assume(status != "Layer already exists")
        assume(status not in ImagePushState.__members__)

        handler = DockerPushResponseHandler(repository="repo", tag="latest")
        e = self.assertRaises(
            DockerServiceError,
            handler._handleImageStatusUpdate,
            json={"status": status, "id": "1", "progressDetail": {}},
        )
        self.assertEqual(str(e), f"Unknown status: {status}")

    def test_handleImageStatusUpdate_preparing(self) -> None:
        imageID = "1"
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        for state in (ImagePushState.start, ImagePushState.preparing):
            # Set prior status
            handler.status[imageID] = ImagePushStatus(
                state=state, currentProgress=0, totalProgress=-1
            )

            handler._handleImageStatusUpdate(
                json={
                    "status": "Preparing",
                    "id": imageID,
                    "progressDetail": {},
                }
            )

            self.assertEqual(
                handler._statusForImage(imageID),
                ImagePushStatus(
                    state=ImagePushState.preparing,
                    currentProgress=0,
                    totalProgress=-1,
                ),
            )

    def test_handleImageStatusUpdate_waiting(self) -> None:
        imageID = "1"
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        for state in (
            ImagePushState.start,
            ImagePushState.preparing,
            ImagePushState.waiting,
        ):
            # Set prior status
            handler.status[imageID] = ImagePushStatus(
                state=state, currentProgress=0, totalProgress=-1
            )

            handler._handleImageStatusUpdate(
                json={"status": "Waiting", "id": imageID, "progressDetail": {}}
            )

            self.assertEqual(
                handler._statusForImage(imageID),
                ImagePushStatus(
                    state=ImagePushState.waiting,
                    currentProgress=0,
                    totalProgress=-1,
                ),
            )

    @given(integers(min_value=0), integers(min_value=0))
    def test_handleImageStatusUpdate_pushing(
        self, currentProgress: int, totalProgress: int
    ) -> None:
        # Ensure totalProgress >= currentProgress
        totalProgress += currentProgress

        imageID = "1"
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        for state in (
            ImagePushState.start,
            ImagePushState.preparing,
            ImagePushState.waiting,
            ImagePushState.pushing,
        ):
            if state <= ImagePushState.waiting:
                priorTotalProgress = -1
            else:
                priorTotalProgress = totalProgress

            # Set prior status
            handler.status[imageID] = ImagePushStatus(
                state=state,
                currentProgress=0,
                totalProgress=priorTotalProgress,
            )

            handler._handleImageStatusUpdate(
                json={
                    "id": imageID,
                    "status": "Pushing",
                    "progressDetail": {
                        "current": currentProgress,
                        "total": totalProgress,
                    },
                },
            )

            self.assertEqual(
                handler._statusForImage(imageID),
                ImagePushStatus(
                    state=ImagePushState.pushing,
                    currentProgress=currentProgress,
                    totalProgress=totalProgress,
                ),
            )

    @given(integers(min_value=0), integers(min_value=0))
    def test_handleImageStatusUpdate_pushing_noTotal(
        self, currentProgress: int, totalProgress: int
    ) -> None:
        # Ensure totalProgress >= currentProgress
        totalProgress += currentProgress

        imageID = "1"
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        for state in (
            ImagePushState.start,
            ImagePushState.preparing,
            ImagePushState.waiting,
            ImagePushState.pushing,
        ):
            priorTotalProgress = -1

            # Set prior status
            handler.status[imageID] = ImagePushStatus(
                state=state,
                currentProgress=0,
                totalProgress=priorTotalProgress,
            )

            handler._handleImageStatusUpdate(
                json={
                    "id": imageID,
                    "status": "Pushing",
                    "progressDetail": {
                        "current": currentProgress,
                    },
                },
            )

            self.assertEqual(
                handler._statusForImage(imageID),
                ImagePushStatus(
                    state=ImagePushState.pushing,
                    currentProgress=currentProgress,
                    totalProgress=priorTotalProgress,
                ),
            )

    @given(integers(min_value=0), integers(min_value=0))
    def test_handleImageStatusUpdate_pushed(
        self, priorCurrentProgress: int, priorTotalProgress: int
    ) -> None:
        # Ensure totalProgress >= currentProgress
        priorTotalProgress += priorCurrentProgress

        imageID = "1"
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        for state in (
            ImagePushState.start,
            ImagePushState.preparing,
            ImagePushState.waiting,
            ImagePushState.pushing,
        ):
            if state <= ImagePushState.waiting:
                _priorCurrentProgress = 0
                _priorTotalProgress = -1
            else:
                _priorCurrentProgress = priorCurrentProgress
                _priorTotalProgress = priorTotalProgress

            # Set prior status
            handler.status[imageID] = ImagePushStatus(
                state=state,
                currentProgress=_priorCurrentProgress,
                totalProgress=_priorTotalProgress,
            )

            handler._handleImageStatusUpdate(
                json={"id": imageID, "status": "Pushed", "progressDetail": {}},
            )

            if state <= ImagePushState.waiting:
                priorTotalProgress = -1

            self.assertEqual(
                handler._statusForImage(imageID),
                ImagePushStatus(
                    state=ImagePushState.pushed,
                    currentProgress=priorTotalProgress,
                    totalProgress=priorTotalProgress,
                ),
            )

    @given(text(), text())
    def test_handleAux(self, tag: str, blob: str) -> None:
        digest = f"sha256:{sha256Hash(blob)}"
        size = len(blob)

        handler = DockerPushResponseHandler(repository="repo", tag=tag)

        handler._handleAux(
            json={
                "progressDetail": {},
                "aux": {"Tag": tag, "Digest": digest, "Size": size},
            },
        )

        self.assertEqual(len(handler.result), 1)
        self.assertEqual(
            handler.result[0],
            ImagePushResult(tag=tag, digest=digest, size=size),
        )

    def test_handleLine_empty(self) -> None:
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        handler._handleLine("   ")

        self.assertEqual(handler.status, {})
        self.assertEqual(handler.errors, [])
        self.assertEqual(handler.result, [])

    def test_handleLine_notJSON(self) -> None:
        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        self.assertRaises(JSONDecodeError, handler._handleLine, "#")

    @given(text(min_size=1))
    def test_handleLine_errorDetail(self, text: str) -> None:
        json = {"errorDetail": text}
        jsonText = jsonTextFromObject(json)

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        e = self.assertRaises(DockerServiceError, handler._handleLine, jsonText)
        self.assertEqual(str(e), text)

    @given(text())
    def test_handleLine_status_image(self, imageID: str) -> None:
        json = {"status": "Preparing", "id": imageID, "progressDetail": {}}
        jsonText = jsonTextFromObject(json)

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        handler._handleLine(jsonText)

        self.assertEqual(
            handler._statusForImage(imageID).state, ImagePushState.preparing
        )

    @given(text())
    def test_handleLine_status_general(self, status: str) -> None:
        tag = "latest"

        # Exclude non-error cases so that we get an error, which has a visible
        # result we can test for.
        assume(
            not status.startswith(DockerPushResponseHandler._repoStatusPrefix)
        )
        assume(not status.startswith(f"{tag}: digest: "))

        json = {"status": status}
        jsonText = jsonTextFromObject(json)

        handler = DockerPushResponseHandler(repository="repo", tag=tag)

        handler._handleLine(jsonText)

        self.assertEqual(
            handler.errors, [f"Unknown push status message: {status!r}"]
        )

    @given(text(), text())
    def test_handleLine_aux(self, tag: str, blob: str) -> None:
        digest = f"sha256:{sha256Hash(blob)}"
        size = len(blob)

        json = {
            "aux": {"Tag": tag, "Digest": digest, "Size": size},
            "progressDetail": {},
        }
        jsonText = jsonTextFromObject(json)

        handler = DockerPushResponseHandler(repository="repo", tag=tag)

        handler._handleLine(jsonText)

        self.assertEqual(
            handler.result,
            [ImagePushResult(tag=tag, digest=digest, size=size)],
        )

    def test_handleLine_unknown(self) -> None:
        jsonText = "{}"

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        e = self.assertRaises(DockerServiceError, handler._handleLine, jsonText)
        self.assertEqual(str(e), f"Unrecognized push response JSON: {jsonText}")

    def test_handlePayload(self) -> None:
        jsonText = b"""
            {"status": "hello"}
            {"status": "goodbye"}
            """

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        handler._handlePayload(jsonText)

        self.assertEqual(
            handler.errors,
            [
                "Unknown push status message: 'hello'",
                "Unknown push status message: 'goodbye'",
            ],
        )

    def test_handlePayload_error(self) -> None:
        jsonText = b"#\n"

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        with logCapture() as events:
            handler._handlePayload(jsonText)

            failureEvents = [
                event
                for event in events
                if event["log_source"] is handler and "failure" in event
            ]

        self.assertEqual(len(failureEvents), 1)

        failureEvent = failureEvents[0]

        self.assertEqual(
            failureEvent["log_format"],
            "While handling push response line: {line}",
        )
        self.assertEqual(failureEvent["line"], "#")

        self.flushLoggedErrors()

    def test_handleResponse_text(self) -> None:
        jsonText = b"""
            {"status": "hello"}
            {"status": "goodbye"}
            """

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        handler.handleResponse(jsonText)

        self.assertEqual(
            handler.errors,
            [
                "Unknown push status message: 'hello'",
                "Unknown push status message: 'goodbye'",
            ],
        )

    def test_handleResponse_generator(self) -> None:
        def jsonText() -> Iterator[bytes]:
            yield b'{"status": "hello"}\n'
            yield b'{"status": "goodbye"}\n'

        handler = DockerPushResponseHandler(repository="repo", tag="latest")

        handler.handleResponse(jsonText())

        self.assertEqual(
            handler.errors,
            [
                "Unknown push status message: 'hello'",
                "Unknown push status message: 'goodbye'",
            ],
        )


@contextmanager
def testingECRServiceClient() -> Iterator[List[ECRServiceClient]]:
    clients: List[ECRServiceClient] = []

    class RememberMeECRServiceClient(ECRServiceClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            clients.append(self)

    Client = ecr.ECRServiceClient
    ecr.ECRServiceClient = cast(  # type: ignore[misc]
        Type, RememberMeECRServiceClient
    )

    try:
        with testingBoto3ECR(), testingDocker():
            yield clients

    finally:
        ecr.ECRServiceClient = Client  # type: ignore[misc]

        clients.clear()


class CommandLineTests(TestCase):
    """
    Tests for the :class:`ECRServiceClient` command line.
    """

    def test_authorization(self) -> None:
        with testingECRServiceClient() as clients:
            # Run "authorization" subcommand
            result = clickTestRun(
                ECRServiceClient.main, ["deploy_aws_ecr", "authorization"]
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]

            # Should not have needed to invoke Docker here
            self.assertFalse(client._dockerClient)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(
            result.echoOutput[0:3],
            [
                ("User: AWS", {}),
                ("Password: see-kreht", {}),
                ("Proxy endpoint: https://101010101010.ecr.aws", {}),
            ],
        )
        self.assertTrue(result.echoOutput[3][0].startswith("Expiration: "))
        self.assertEqual(len(result.echoOutput), 4)
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    def test_list(self) -> None:
        with testingECRServiceClient() as clients:
            # Run "authorization" subcommand
            result = clickTestRun(
                ECRServiceClient.main, ["deploy_aws_ecr", "list"]
            )

            self.assertEqual(len(clients), 1)

        expectedEchoOutput: ClickTestResult.echoOutputType = []
        for image in MockDockerClient._defaultLocalImages():
            tags = ", ".join(image.tags)
            expectedEchoOutput.append((f"{image.id}: {tags}", {}))

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, expectedEchoOutput)
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    def test_tag(self) -> None:
        with testingECRServiceClient() as clients:
            existingName = "image:1"
            newName = "test:latest"

            # Run "authorization" subcommand
            result = clickTestRun(
                ECRServiceClient.main,
                ["deploy_aws_ecr", "tag", existingName, newName],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]

            image = client.imageWithName(existingName)
            self.assertIn(newName, image.tags)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    def test_push(self) -> None:
        with testingECRServiceClient() as clients:
            existingName = "image:1"
            ecrName = "cloud:latest"

            # Run "authorization" subcommand
            result = clickTestRun(
                ECRServiceClient.main,
                ["deploy_aws_ecr", "push", existingName, ecrName],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]

            # ECR tag should exist both locally and in ECR
            self.assertIsNotNone(client.imageWithName(ecrName))
            self.assertIsNotNone(client._docker.images._fromECR(ecrName))

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")
