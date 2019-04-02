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
from datetime import (
    datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
)
from ssl import Options as SSLOptions
from typing import (
    Any, ClassVar, Dict, Iterator, List, Optional, Sequence, Tuple
)

from attr import Attribute, attrib, attrs

from twisted.trial.unittest import SynchronousTestCase as TestCase

from .. import ecr
from ..ecr import ECRAuthorizationToken, ECRServiceClient


__all__ = ()


def utcNow() -> DateTime:
    return DateTime.utcnow().replace(tzinfo=TimeZone.utc)



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
class MockDockerClient(object):
    """
    Mock Docker client.
    """

    #
    # Class attributes
    #

    @classmethod
    def _fromEnvironment(
        cls, ssl_version: Optional[SSLOptions] = None
    ) -> "MockDockerClient":
        return cls(sslVersion=ssl_version)


    @classmethod
    def _setUp(cls) -> None:
        pass


    @classmethod
    def _tearDown(cls) -> None:
        pass


    #
    # Instance attributes
    #

    _sslVersion: SSLOptions
    _login: Optional[Tuple[str, str, str]] = None


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


    def test_login(self) -> None:
        with testingBoto3ECR(), testingDocker():
            client = ECRServiceClient()
            client.login()
