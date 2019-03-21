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
from ..ecs import ECSServiceClient, NoChangesError, TaskDefinition


__all__ = ()



@attrs(auto_attribs=True, slots=True)
class MockBoto3Client(object):
    """
    Mock Boto3 client.
    """

    _awsService: str = attrib()
    _currentTaskARN = "arn:mock:task-definition/ranger-service:1"
    _currentImageName = "/rangers/ranger-clubhouse-api:1"
    _currentEnvironment = {
        "VARIABLE1": "value1",
        "VARIABLE2": "value2",
    }
    _taskDefinitions: Dict[str, TaskDefinition] = {}


    @_awsService.validator
    def _validate_service(self, attribute: Attribute, value: Any) -> None:
        assert value == "ecs"


    def __attrs_post_init__(self) -> None:
        self._taskDefinitions[self._currentTaskARN] = {
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
                    "environment": ECSServiceClient._environmentAsJSON(
                        self._currentEnvironment
                    ),
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
        }


    def describe_services(
        self, cluster: str, services: List[str]
    ) -> Dict[str, List[Dict[str, str]]]:
        assert len(services) == 1
        return {"services": [{"taskDefinition": self._currentTaskARN}]}


    def describe_task_definition(
        self, taskDefinition: str
    ) -> Dict[str, TaskDefinition]:
        return {"taskDefinition": self._taskDefinitions[taskDefinition]}


    def register_task_definition(
        self, **taskDefinition: Any
    ) -> Dict[str, TaskDefinition]:
        # Come up with a new task ARN
        maxVersion = 0
        for arn in self._taskDefinitions:
            version = int(arn.split(":")[-1])
            if version > maxVersion:
                maxVersion = version

        arn = f'{"".join(self._currentTaskARN.split(":")[:-1])}{maxVersion+1}'
        taskDefinition["taskDefinitionArn"] = arn
        self._taskDefinitions[arn] = taskDefinition

        return {"taskDefinition": taskDefinition}



class ECSServiceClientTests(TestCase):
    """
    Tests for :class:`ECSServiceClient`
    """

    def test_environmentAsJSON(self) -> None:
        raise NotImplementedError()

    test_environmentAsJSON.todo = "not implemented"


    def test_environmentFromJSON(self) -> None:
        raise NotImplementedError()

    test_environmentFromJSON.todo = "not implemented"


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
        self.assertEqual(arn, client._client._currentTaskARN)


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
        self.assertEqual(imageName, client._client._currentImageName)


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

        self.assertEqual(
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
        expectedEnvironment = dict(newEnvironment)

        # TASK_UPDATED is inserted during updates.
        self.assertIn("TASK_UPDATED", updatedEnvironment)
        expectedEnvironment["TASK_UPDATED"] = (
            updatedEnvironment["TASK_UPDATED"]
        )

        self.assertEqual(updatedEnvironment, expectedEnvironment)


    def test_updateTaskDefinition_travis(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        # Patch the (local) system environment to pretend we're in Travis CI
        travisEnvironment = {
            "TRAVIS": "true",
            "TRAVIS_COMMIT": "0" * 40,
            "TRAVIS_COMMIT_MESSAGE": "Fixed some stuff",
            "TRAVIS_JOB_WEB_URL": "https://travis-ci.com/o/r/builds/0",
            "TRAVIS_PULL_REQUEST_BRANCH": "1",
            "TRAVIS_TAG": "v0.0.0",
        }
        self.patch(ecs, "environ", travisEnvironment)

        # Make an unrelated change to avoid NoChangesError
        newTaskDefinition = client.updateTaskDefinition(
            imageName=f"{client.currentImageName()}0"
        )
        updatedEnvironment = dict(client._environmentFromJSON(
            newTaskDefinition["containerDefinitions"][0]["environment"]
        ))
        expectedEnvironment = dict(client._client._currentEnvironment)
        expectedEnvironment.update(travisEnvironment)
        del expectedEnvironment["TRAVIS"]

        # TASK_UPDATED is inserted during updates.
        self.assertIn("TASK_UPDATED", updatedEnvironment)
        expectedEnvironment["TASK_UPDATED"] = (
            updatedEnvironment["TASK_UPDATED"]
        )

        self.assertEqual(updatedEnvironment, expectedEnvironment)


    def test_registerTaskDefinition(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        newImageName = f"{client.currentImageName()}0"
        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        arn = client.registerTaskDefinition(newTaskDefinition)
        registeredTaskDefinition = (
            client._client.describe_task_definition(arn)["taskDefinition"]
        )

        expectedTaskDefinition = dict(newTaskDefinition)
        expectedTaskDefinition["taskDefinitionArn"] = arn

        self.maxDiff = None

        self.assertEqual(registeredTaskDefinition, expectedTaskDefinition)


    def test_currentTaskEnvironment(self) -> None:
        raise NotImplementedError()

    test_currentTaskEnvironment.todo = "not implemented"


    def test_updateTaskEnvironment(self) -> None:
        raise NotImplementedError()

    test_updateTaskEnvironment.todo = "not implemented"


    def test_deployTask(self) -> None:
        raise NotImplementedError()

    test_deployTask.todo = "not implemented"


    def test_deployTaskDefinition(self) -> None:
        raise NotImplementedError()

    test_deployTaskDefinition.todo = "not implemented"


    def test_deployImage(self) -> None:
        raise NotImplementedError()

    test_deployImage.todo = "not implemented"


    def test_deployTaskEnvironment(self) -> None:
        raise NotImplementedError()

    test_deployTaskEnvironment.todo = "not implemented"


    def test_rollback(self) -> None:
        raise NotImplementedError()

    test_rollback.todo = "not implemented"
