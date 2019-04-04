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
from ssl import Options as SSLOptions
from typing import (
    Any, ClassVar, Dict, Iterator, List, Optional, Sequence, Tuple, Type, cast
)

from attr import Attribute, Factory, attrib, attrs

from docker.errors import ImageNotFound

from twisted.trial.unittest import SynchronousTestCase as TestCase

from deploy.ext.click import ClickTestResult, clickTestRun

from .. import ecr
from ..ecr import (
    ECRAuthorizationToken, ECRServiceClient, InvalidImageNameError, utcNow
)


__all__ = ()



@attrs(auto_attribs=True)
class MockBoto3ECRClient(object):
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

        token = b64encode("AWS:see-kreht".encode("utf-8"))
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
class MockImage(object):
    id: str
    tags: List[str]


    def tag(self, repository: str, tag: Optional[str] = None) -> bool:
        assert ":" not in repository
        assert tag is not None
        name = f"{repository}:{tag}"
        self.tags.append(name)
        return True



@attrs(auto_attribs=True)
class MockImagesAPI(object):
    _parent: "MockDockerClient"


    def list(self) -> List[MockImage]:
        return self._parent._localImages


    def get(self, name: str) -> MockImage:
        for image in self.list():
            if name in image.tags:
                return image

        raise ImageNotFound(name)


    def push(
        self, repository: str, tag: Optional[str] = None,
        stream: bool = False, decode: bool = False,
        auth_config: Optional[Dict[str, str]] = None,
    ) -> str:
        assert ":" not in repository
        assert tag is not None

        assert not stream, "streaming not implemented"
        assert not decode, "decode not implemented"

        raise NotImplementedError()

        return ""



def hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()



@attrs(auto_attribs=True)
class MockDockerClient(object):
    """
    Mock Docker client.
    """

    @staticmethod
    def _defaultLocalImages() -> List[MockImage]:
        return [
            MockImage(id=hash("1"), tags=["image:1"]),
            MockImage(id=hash("2"), tags=["image:2"]),
            MockImage(id=hash("3"), tags=["image:3"]),
        ]

    #
    # Class attributes
    #

    _localImages: ClassVar[List[MockImage]] = []


    @classmethod
    def _fromEnvironment(
        cls, ssl_version: Optional[SSLOptions] = None
    ) -> "MockDockerClient":
        return cls(sslVersion=ssl_version)


    @classmethod
    def _setUp(cls) -> None:
        # Copy images so that we aren't sharing mutable tags
        cls._localImages.extend(cls._defaultLocalImages())


    @classmethod
    def _tearDown(cls) -> None:
        cls._localImages.clear()


    #
    # Instance attributes
    #

    _sslVersion: SSLOptions
    _login: Optional[Tuple[str, str, str]] = None

    images: MockImagesAPI = Factory(MockImagesAPI, takes_self=True)


    def login(
        self, username: str, password: str, registry: str, reauth: bool
    ) -> Dict[str, str]:
        self._login = (username, password, registry)
        return {
            "IdentityToken": "",
            "Status": "Login Succeeded",
        }



@contextmanager
def testingBoto3ECR() -> Iterator[None]:
    MockBoto3ECRClient._setUp()

    boto3Client = ecr.boto3Client
    ecr.boto3Client = MockBoto3ECRClient

    yield

    ecr.boto3Client = boto3Client

    MockBoto3ECRClient._tearDown()


@contextmanager
def testingDocker() -> Iterator[None]:
    MockDockerClient._setUp()

    dockerClientFromEnvironment = ecr.dockerClientFromEnvironment
    ecr.dockerClientFromEnvironment = MockDockerClient._fromEnvironment

    yield

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
            self.assertEqual(
                client._tlsVersion, ECRServiceClient._tlsVersion
            )


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


    # def test_login(self) -> None:
    #     with testingBoto3ECR(), testingDocker():
    #         client = ECRServiceClient()
    #         assert client._docker._login is None

    #         client.login()

    #         self.assertIsNotNone(client._docker._login)


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
            self.assertRaises(
                InvalidImageNameError, client.imageWithName, "image"
            )


    def test_imageWithName_notFound(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                ImageNotFound, client.imageWithName, "xyzzy:fnord"
            )


    def test_tag(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            image = client.imageWithName("image:1")
            assert image.tags == ["image:1"]

            client.tag("image:1", "test:latest")
            image = client.imageWithName("image:1")
            self.assertIn("test:latest", image.tags)


    def test_imageWithName_invalidExisting(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                InvalidImageNameError, client.tag, "image", "test:latest"
            )


    def test_tag_invalidNew(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                InvalidImageNameError, client.tag, "image:1", "test"
            )


    def test_tag_doesntExist(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                ImageNotFound, client.tag, "xyzzy:fnord", "test:latest"
            )


    def test_push(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            client.push("image:1", "test:latest")
            raise NotImplementedError()

    test_push.todo = "unimplemented"


    def test_push_invalidLocal(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                InvalidImageNameError, client.push, "image", "test:latest"
            )


    def test_push_invalidECR(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                InvalidImageNameError, client.push, "image:1", "test"
            )


    def test_push_doesntExist(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            self.assertRaises(
                ImageNotFound, client.push, "xyzzy:fnord", "test:latest"
            )



@contextmanager
def testingECRServiceClient() -> Iterator[List[ECRServiceClient]]:
    clients: List[ECRServiceClient] = []

    class RememberMeECRServiceClient(ECRServiceClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            clients.append(self)

    Client = ecr.ECRServiceClient
    ecr.ECRServiceClient = cast(Type, RememberMeECRServiceClient)

    with testingBoto3ECR(), testingDocker():
        yield clients

    ecr.ECRServiceClient = Client

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


    # def test_login(self) -> None:
    #     with testingECRServiceClient() as clients:
    #         # Run "login" subcommand
    #         result = clickTestRun(
    #             ECRServiceClient.main, ["deploy_aws_ecr", "login"]
    #         )

    #         self.assertEqual(len(clients), 1)
    #         client = clients[0]

    #         self.assertIsNotNone(client._docker._login)

    #     self.assertEqual(result.exitCode, 0)
    #     self.assertEqual(result.echoOutput, [])
    #     self.assertEqual(result.stdout.getvalue(), "")
    #     self.assertEqual(result.stderr.getvalue(), "")


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
            expectedEchoOutput.append(
                (f"{image.id}: {tags}", {})
            )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, expectedEchoOutput)
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")


    def test_tag(self) -> None:
        with testingECRServiceClient() as clients:
            # Run "authorization" subcommand
            result = clickTestRun(
                ECRServiceClient.main, [
                    "deploy_aws_ecr", "tag", "image:1", "test:latest"
                ]
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]

            image = client.imageWithName("image:1")
            self.assertIn("test:latest", image.tags)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")
