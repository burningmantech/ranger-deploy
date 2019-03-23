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

import sys
from copy import deepcopy
from typing import (
    Any, Callable, Dict, Iterable, List,
    Mapping, Optional, Sequence, Set, Tuple,
)

from attr import Attribute, Factory, attrib, attrs

from hypothesis import assume, given
from hypothesis.strategies import (
    composite, dictionaries, integers, just, sets, text
)

from twisted.trial.unittest import SynchronousTestCase as TestCase

from .. import ecs
from ..ecs import (
    ECSServiceClient, NoChangesError,
    TaskDefinition, TaskEnvironment, TaskEnvironmentUpdates,
)


__all__ = ()


_defaultTaskDefinitions: List[TaskDefinition] = [
    {
        "taskDefinitionArn": "arn:mock:task-definition/service:0",
        "family": "service-fg",
        "revision": 1,
        "containerDefinitions": [
            {
                "name": "service-container",
                "image": "/team/service-project:1000",
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
                "environment": [],
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
    {
        "taskDefinitionArn": "arn:mock:task-definition/service:1",
        "family": "service-fg",
        "revision": 1,
        "containerDefinitions": [
            {
                "name": "service-container",
                "image": "/team/service-project:1001",
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
]


def environment_updates(
    min_size: int = 0, max_size: Optional[int] = None
) -> Callable:
    return dictionaries(
        text(min_size=1), text(),
        min_size=min_size, max_size=max_size,
    )


@composite
def set_unset_envs(draw: Callable) -> Tuple[Dict[str, str], Set[str]]:
    updates = draw(environment_updates(min_size=1))
    removes = draw(sets(elements=just(tuple(updates.keys()))))
    return (updates, removes)


@attrs(auto_attribs=True)
class MockBoto3Client(object):
    """
    Mock Boto3 client.
    """

    _awsService: str = attrib()
    @_awsService.validator
    def _validate_service(self, attribute: Attribute, value: Any) -> None:
        assert value == "ecs"


    _taskDefinitions: List[TaskDefinition] = Factory(
        lambda: deepcopy(_defaultTaskDefinitions)
    )
    _currentTaskARN = "arn:mock:task-definition/service:1"


    def _taskDefinitionWithARN(self, arn: str) -> TaskDefinition:
        for taskDefinition in self._taskDefinitions:
            if taskDefinition["taskDefinitionArn"] == arn:
                return taskDefinition

        raise AssertionError(f"Task definition {arn} not found")


    def _listTaskDefinitions(self) -> Iterable[str]:
        for taskDefinition in self._taskDefinitions:
            yield taskDefinition["taskDefinitionArn"]


    @property
    def _currentTaskDefinition(self) -> TaskDefinition:
        return self._taskDefinitionWithARN(self._currentTaskARN)


    @property
    def _currentContainerDefinition(self) -> Mapping[str, Any]:
        return self._currentTaskDefinition["containerDefinitions"][0]


    @property
    def _currentImageName(self) -> str:
        return self._currentContainerDefinition["image"]


    @property
    def _currentEnvironment(self) -> TaskEnvironment:
        return ECSServiceClient._environmentFromJSON(
            self._currentContainerDefinition["environment"]
        )


    @property
    def _currentCompatibilities(self) -> Sequence[str]:
        return self._currentTaskDefinition["compatibilities"]


    @property
    def _currentRequiresAttributes(self) -> Sequence[Mapping[str, str]]:
        return self._currentTaskDefinition["requiresAttributes"]


    def describe_services(
        self, cluster: str, services: Sequence[str]
    ) -> Mapping[str, Sequence[Mapping[str, str]]]:
        assert len(services) == 1
        return {"services": [{"taskDefinition": self._currentTaskARN}]}


    def describe_task_definition(
        self, taskDefinition: str
    ) -> Mapping[str, TaskDefinition]:
        return {"taskDefinition": self._taskDefinitionWithARN(taskDefinition)}


    def list_task_definitions(
        self, familyPrefix: str
    ) -> Mapping[str, Sequence[str]]:
        return {
            "taskDefinitionArns": list(
                t["taskDefinitionArn"] for t in self._taskDefinitions
                if t["family"].startswith(familyPrefix)
            )
        }


    def register_task_definition(
        self, **taskDefinition: Any
    ) -> Mapping[str, TaskDefinition]:
        # Come up with a new task ARN
        maxVersion = 0
        for arn in self._listTaskDefinitions():
            version = int(arn.split(":")[-1])
            if version > maxVersion:
                maxVersion = version

        arn = (
            f'{":".join(self._currentTaskARN.split(":")[:-1])}'
            f":{maxVersion + 1}"
        )

        taskDefinition["taskDefinitionArn"] = arn
        taskDefinition["revision"] = maxVersion + 1
        taskDefinition["status"] = "ACTIVE"
        taskDefinition["compatibilities"] = self._currentCompatibilities
        taskDefinition["requiresAttributes"] = self._currentRequiresAttributes

        self._taskDefinitions.append(taskDefinition)

        return {"taskDefinition": taskDefinition}


    def update_service(
        self, cluster: str, service: str, taskDefinition: str
    ) -> None:
        assert taskDefinition in self._listTaskDefinitions()
        self._currentTaskARN = taskDefinition


class ECSServiceClientTests(TestCase):
    """
    Tests for :class:`ECSServiceClient`
    """

    def setUp(self) -> None:
        self.patch(ecs, "Boto3Client", MockBoto3Client)


    def test_environmentAsJSON(self) -> None:
        self.assertEqual(
            ECSServiceClient._environmentAsJSON(
                {"foo": "bar", "x": "1"}
            ),
            [{"name": "foo", "value": "bar"}, {"name": "x", "value": "1"}]
        )


    def test_environmentFromJSON(self) -> None:
        self.assertEqual(
            ECSServiceClient._environmentFromJSON(
                [{"name": "foo", "value": "bar"}, {"name": "x", "value": "1"}]
            ),
            {"foo": "bar", "x": "1"}
        )


    def test_client(self) -> None:
        """
        :meth:`ECSServiceClient._client` property returns a client.
        """
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        self.assertIsInstance(client._client, MockBoto3Client)


    def test_currentTaskARN(self) -> None:
        """
        :meth:`ECSServiceClient.currentTaskARN` returns the ARN of the current
        task.
        """
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        arn = client.currentTaskARN()
        self.assertEqual(arn, client._client._currentTaskARN)


    def test_currentTaskDefinition(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        taskDefinition = client.currentTaskDefinition()
        self.assertIsInstance(taskDefinition, dict)
        self.assertTrue(taskDefinition.get("family"))
        self.assertTrue(taskDefinition.get("revision"))
        self.assertTrue(taskDefinition.get("containerDefinitions"))
        self.assertIn("FARGATE", taskDefinition.get("compatibilities", []))


    def test_currentImageName(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")
        imageName = client.currentImageName()
        self.assertEqual(imageName, client._client._currentImageName)


    @given(integers(min_value=2))
    def test_updateTaskDefinition_updated(self, tag: int) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        repo, oldTag = client.currentImageName().split(":")
        assume(int(oldTag) != tag)
        newImageName = f"{repo}:{tag}"

        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        self.assertEqual(
            client._taskImageName(newTaskDefinition), newImageName
        )


    def test_updateTaskDefinition_none(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        self.assertRaises(NoChangesError, client.updateTaskDefinition)


    def test_updateTaskDefinition_same(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        self.assertRaises(
            NoChangesError,
            client.updateTaskDefinition, imageName=client.currentImageName()
        )


    @given(environment_updates(min_size=1))
    def test_updateTaskDefinition_updateEnvironment(
        self, newEnvironment: TaskEnvironment
    ) -> None:
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
            imageName=f"{client.currentImageName()}4027"
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
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        repo, tag = client.currentImageName().split(":")
        newImageName = f"{repo}:{tag}1987"

        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        arn = client.registerTaskDefinition(newTaskDefinition)
        registeredTaskDefinition = (
            client._client.describe_task_definition(arn)["taskDefinition"]
        )

        expectedTaskDefinition = dict(newTaskDefinition)
        expectedTaskDefinition["taskDefinitionArn"] = arn
        expectedTaskDefinition["revision"] = int(arn.split(":")[-1])
        expectedTaskDefinition["status"] = "ACTIVE"
        expectedTaskDefinition["compatibilities"] = (
            client._client._currentCompatibilities
        )
        expectedTaskDefinition["requiresAttributes"] = (
            client._client._currentRequiresAttributes
        )

        self.assertEqual(registeredTaskDefinition, expectedTaskDefinition)


    def test_currentTaskEnvironment(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        self.assertEqual(
            client.currentTaskEnvironment(), client._client._currentEnvironment
        )


    @given(dictionaries(text(), text()))
    def test_updateTaskEnvironment_set(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        newEnvironment = client.updateTaskEnvironment(updates)
        expectedEnvironment = dict(client._client._currentEnvironment)
        expectedEnvironment.update(updates)

        self.assertEqual(newEnvironment, expectedEnvironment)


    @given(set_unset_envs())
    def test_updateTaskEnvironment_unset(
        self, instructions: Tuple[TaskEnvironmentUpdates, Set[str]]
    ) -> None:
        updates, removes = instructions

        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedEnvironment = dict(client.currentTaskEnvironment())
        for key, value in updates.items():
            if key in removes:
                if key in expectedEnvironment:
                    del expectedEnvironment[key]
            else:
                assert value is not None
                expectedEnvironment[key] = value

        # Deploy the input updates and get back the result
        client.deployTaskEnvironment(updates)
        newEnvironment = client.updateTaskEnvironment(
            {k: None for k in removes}
        )

        expectedEnvironment["TASK_UPDATED"] = newEnvironment["TASK_UPDATED"]

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_deployTask(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        newImageName = f"{client.currentImageName()}1934"
        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        arn = client.registerTaskDefinition(newTaskDefinition)
        client.deployTask(arn)

        self.assertEqual(client._client._currentTaskARN, arn)


    def test_deployTaskDefinition(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        newImageName = f"{client.currentImageName()}9347"
        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        client.deployTaskDefinition(newTaskDefinition)

        newTaskDefinition = client.currentTaskDefinition()

        self.assertEqual(
            ECSServiceClient._taskImageName(newTaskDefinition), newImageName
        )


    def test_deployImage_new(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        newImageName = f"{client.currentImageName()}1046"

        client.deployImage(newImageName)

        newTaskDefinition = client.currentTaskDefinition()

        self.assertEqual(
            ECSServiceClient._taskImageName(newTaskDefinition), newImageName
        )


    def test_deployImage_same(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedImageName = client.currentImageName()
        client.deployImage(expectedImageName)
        self.assertEqual(client.currentImageName(), expectedImageName)


    @given(environment_updates(min_size=1))
    def test_deployTaskEnvironment_updates(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedEnvironment = dict(client._client._currentEnvironment)
        expectedEnvironment.update(updates)

        client.deployTaskEnvironment(updates)
        newEnvironment = client._client._currentEnvironment

        expectedEnvironment["TASK_UPDATED"] = newEnvironment["TASK_UPDATED"]

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_deployTaskEnvironment_none(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedEnvironment = dict(client._client._currentEnvironment)

        client.deployTaskEnvironment({})
        newEnvironment = client._client._currentEnvironment

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_deployTaskEnvironment_same(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedEnvironment = dict(client._client._currentEnvironment)

        client.deployTaskEnvironment(expectedEnvironment)
        newEnvironment = client._client._currentEnvironment

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_rollback(self) -> None:
        client = ECSServiceClient(cluster="MyCluster", service="MyService")

        expectedImageName = client.currentImageName()
        newImageName = f"{expectedImageName}2957"

        client.deployImage(newImageName)
        client.rollback()

        self.assertEqual(client.currentImageName(), expectedImageName)



class CommandLineTests(TestCase):
    """
    Tests for the command line.
    """

    def setUp(self) -> None:
        # Patch exit in case of usage errors
        self.exitStatus: List[int] = []
        self.patch(sys, "exit", lambda code=None: self.exitStatus.append(code))

        # Patch boto3 client
        self.patch(ecs, "Boto3Client", MockBoto3Client)

        # Patch ECSServiceClient constructor so we can track usage
        self.clients: List[ECSServiceClient] = []

        class RememberMeECSServiceClient(ECSServiceClient):
            def __init__(innerSelf, cluster: str, service: str) -> None:
                super().__init__(cluster=cluster, service=service)
                self.clients.append(innerSelf)

        self.patch(ecs, "ECSServiceClient", RememberMeECSServiceClient)


    @given(text(min_size=1), text(min_size=1), text(min_size=1))
    def test_cli_staging(
        self, stagingCluster: str, stagingService: str, imageName: str,
    ) -> None:
        # Because hypothesis and multiple runs
        self.exitStatus.clear()
        self.clients.clear()

        self.patch(sys, "argv", [
            "deploy_aws", "staging",
            "--staging-cluster", stagingCluster,
            "--staging-service", stagingService,
            "--image", imageName,
        ])
        ECSServiceClient.main()

        self.assertEqual(self.exitStatus, [0])
        self.assertEqual(len(self.clients), 1)

        client = self.clients[0]

        self.assertEqual(client.cluster, stagingCluster)
        self.assertEqual(client.service, stagingService)
        self.assertEqual(client.currentImageName(), imageName)


    @given(text(min_size=1), text(min_size=1))
    def test_cli_rollback(
        self, stagingCluster: str, stagingService: str
    ) -> None:
        # Because hypothesis and multiple runs
        self.exitStatus.clear()
        self.clients.clear()

        self.patch(sys, "argv", [
            "deploy_aws", "rollback",
            "--staging-cluster", stagingCluster,
            "--staging-service", stagingService,
        ])
        ECSServiceClient.main()

        self.assertEqual(self.exitStatus, [0])
        self.assertEqual(len(self.clients), 1)

        client = self.clients[0]

        self.assertEqual(client.cluster, stagingCluster)
        self.assertEqual(client.service, stagingService)
        self.assertEqual(
            client.currentImageName(), "/team/service-project:1000"
        )
