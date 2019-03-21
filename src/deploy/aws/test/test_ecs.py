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

from attr import Attribute, attrs, attrib
from typing import Any, Dict, List

from boto3 import client as Boto3Client

from twisted.trial.unittest import SynchronousTestCase as TestCase

from .. import ecs
from ..ecs import ECSServiceClient


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
    def _validate_service(self, attribute: Attribute, value: Any):
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
