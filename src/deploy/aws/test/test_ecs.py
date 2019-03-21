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
Tests for :mod:`deploy.aws.ecs`
"""

from typing import Any, Dict, List

from attr import Attribute, attrib, attrs

from hypothesis import assume, given
from hypothesis.strategies import dictionaries, integers, text

from twisted.trial.unittest import SynchronousTestCase as TestCase

from .. import ecs
from ..ecs import ECSServiceClient, NoChangesError


__all__ = ()



@attrs(auto_attribs=True, slots=True)
class MockBoto3Client(object):
    """
    Mock Boto3 client.
    """

    _awsService: str = attrib()
    _currentTaskARN = "arn:mock:task-definition/ranger-service:1"
    _currentImageName = "/rangers/ranger-clubhouse-api:1"


    @_awsService.validator
    def _validate_service(self, attribute: Attribute, value: Any) -> None:
        assert value == "ecs"


    def describe_services(
        self, cluster: str, services: List[str]
    ) -> Dict[str, List[Dict[str, str]]]:
        assert len(services) == 1
        return {"services": [{"taskDefinition": self._currentTaskARN}]}


    def describe_task_definition(
        self, taskDefinition: str
    ) -> Dict[str, Dict]:
        return {
            "taskDefinition": {
                "taskDefinitionArn": self._currentTaskARN,
                "family": "ranger-service-fg",
                "revision": 1,
                "containerDefinitions": [
                    {
                        "name": "ranger-service-container",
                        "image": self._currentImageName,
                        "cpu": 0,
                        "memory": 128,
                        "portMappings": [
                            {
                                "containerPort": 80,
                                "hostPort": 80,
                                "protocol": "tcp",
                            },
                        ],
                        "essential": True,
                        "environment": [
                            {"name": "VARIABLE1", "value": "value1"},
                            {"name": "VARIABLE2", "value": "value2"},
                        ],
                        "mountPoints": [],
                        "volumesFrom": [],
                    },
                ],
                "taskRoleArn": "arn:mock:role/ecsTaskExecutionRole",
                "executionRoleArn": "arn:mock:role/ecsTaskExecutionRole",
                "networkMode": "awsvpc",
                "volumes": [],
                "status": "ACTIVE",
                "requiresAttributes": [
                    {"name": "ecs.capability.execution-role-ecr-pull"},
                    {"name": "com.amazonaws.ecs.capability.ecr-auth"},
                    {"name": "com.amazonaws.ecs.capability.task-iam-role"},
                ],
                "placementConstraints": [],
                "compatibilities": ["EC2", "FARGATE"],
                "requiresCompatibilities": ["FARGATE"],
                "cpu": "256",
                "memory": "512",
            },
        }


class ECSServiceClientTests(TestCase):
    """
    Tests for :class:`ECSServiceClient`
    """

    def test_client(self) -> None:
        """
        :meth:`ECSServiceClient._client` property returns a client.
        """
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        self.assertIsInstance(client._client, MockBoto3Client)


    def test_currentTaskARN(self) -> None:
        """
        :meth:`ECSServiceClient.currentTaskARN` returns the ARN of the current
        task.
        """
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        arn = client.currentTaskARN()
        self.assertEquals(arn, client._client._currentTaskARN)


    def test_currentTaskDefinition(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        taskDefinition = client.currentTaskDefinition()
        self.assertIsInstance(taskDefinition, dict)
        self.assertTrue(taskDefinition.get("family"))
        self.assertTrue(taskDefinition.get("revision"))
        self.assertTrue(taskDefinition.get("containerDefinitions"))
        self.assertIn("FARGATE", taskDefinition.get("compatibilities", []))


    def test_currentImageName(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        imageName = client.currentImageName()
        self.assertEquals(imageName, client._client._currentImageName)


    def test_updateTaskDefinition_noChanges(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        self.assertRaises(NoChangesError, client.updateTaskDefinition)


    @given(integers(min_value=2))
    def test_updateTaskDefinition_updateImage(self, tag: int) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        repo = client.currentImageName().split(":")[0]
        newImageName = f"{repo}:{tag}"

        newTaskDefinition = client.updateTaskDefinition(newImageName)

        self.assertEquals(
            newTaskDefinition["containerDefinitions"][0]["image"], newImageName
        )


    @given(dictionaries(text(), text()))
    def test_updateTaskDefinition_updateEnvironment(
        self, newEnvironment: Dict[str, str]
    ) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        # TRAVIS environment variable makes Travis-CI things happen which we
        # aren't testing for here.
        assume("TRAVIS" not in newEnvironment)

        newTaskDefinition = client.updateTaskDefinition(
            environment=newEnvironment
        )
        updatedEnvironment = dict(client._environmentFromJSON(
            newTaskDefinition["containerDefinitions"][0]["environment"]
        ))

        # TASK_UPDATED is inserted during updates.
        del updatedEnvironment["TASK_UPDATED"]

        self.assertEquals(updatedEnvironment, newEnvironment)
