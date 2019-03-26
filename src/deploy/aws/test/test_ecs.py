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
    Any, Callable, ClassVar, Dict, List,
    Mapping, Optional, Sequence, Set, Tuple,
)

from attr import Attribute, attrib, attrs

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

    #
    # Class attributes
    #

    _sampleClusterStaging:    ClassVar = "staging-cluster"
    _sampleServiceStaging:    ClassVar = "staging-service-fg"
    _sampleClusterProduction: ClassVar = "production-cluster"
    _sampleServiceProduction: ClassVar = "production-service-fg"

    _defaultARNNamespace: ClassVar = "arn:mock:task-definition/service"

    _defaultCompatibilities: ClassVar[Sequence[str]] = ["EC2", "FARGATE"]

    _defaultRequiresAttributes: ClassVar[Sequence[Mapping[str, str]]] = [
        {"name": "ecs.capability.execution-role-ecr-pull"},
        {"name": "com.amazonaws.ecs.capability.ecr-auth"},
        {"name": "com.amazonaws.ecs.capability.task-iam-role"},
    ]

    _defaultTaskDefinitions: ClassVar[Sequence[TaskDefinition]] = [
        {
            "taskDefinitionArn": f"{_defaultARNNamespace}:0",
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
            "requiresAttributes": deepcopy(_defaultRequiresAttributes),
            "placementConstraints": [],
            "compatibilities": list(_defaultCompatibilities),
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
        },
        {
            "taskDefinitionArn": f"{_defaultARNNamespace}:1",
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
            "requiresAttributes": deepcopy(_defaultRequiresAttributes),
            "placementConstraints": [],
            "compatibilities": list(_defaultCompatibilities),
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
        },
    ]

    _taskDefinitions: ClassVar[Dict[str, TaskDefinition]] = {}
    _services: ClassVar[Dict[str, Dict[str, str]]] = {}


    @classmethod
    def _addDefaultTaskDefinitions(cls) -> None:
        for taskDefinition in cls._defaultTaskDefinitions:
            cls._taskDefinitions[taskDefinition["taskDefinitionArn"]] = (
                deepcopy(taskDefinition)
            )


    @classmethod
    def _clearTaskDefinitions(cls) -> None:
        cls._taskDefinitions = {}


    @classmethod
    def _addCluster(cls, cluster: str) -> None:
        if cluster in cls._services:
            raise AssertionError(f"Cluster {cluster!r} already exists")
        cls._services[cluster] = {}


    @classmethod
    def _addService(cls, cluster: str, service: str, arn: str) -> None:
        if service in cls._services[cluster]:
            raise AssertionError(
                f"Service {service!r} already exists in cluster {cluster!r}"
            )
        cls._services[cluster][service] = arn


    @classmethod
    def _addDefaultServices(cls) -> None:
        cls._addCluster(cls._sampleClusterStaging)
        cls._addService(
            cls._sampleClusterStaging,
            cls._sampleServiceStaging,
            cls._defaultTaskDefinitions[-1]["taskDefinitionArn"]
        )

        cls._addCluster(cls._sampleClusterProduction)
        cls._addService(
            cls._sampleClusterProduction,
            cls._sampleServiceProduction,
            cls._defaultTaskDefinitions[-1]["taskDefinitionArn"]
        )


    @classmethod
    def _clearServices(cls) -> None:
        cls._services = {}


    @classmethod
    def _addDefaultData(cls) -> None:
        cls._addDefaultTaskDefinitions()
        cls._addDefaultServices()


    @classmethod
    def _clearData(cls) -> None:
        cls._clearServices()
        cls._clearTaskDefinitions()


    @classmethod
    def _currentTaskARN(cls, cluster: str, service: str) -> str:
        if cluster not in cls._services:
            raise AssertionError(
                f"Cluster {cluster!r} not in {cls._services.keys()}"
            )
        if service not in cls._services[cluster]:
            raise AssertionError(
                f"Service {service!r} not in {cls._services[cluster].keys()}"
            )
        return cls._services[cluster][service]


    @classmethod
    def _setCurrentTaskARN(cls, cluster: str, service: str, arn: str) -> None:
        cls._services[cluster][service] = arn


    @classmethod
    def _currentTaskDefinition(
        cls, cluster: str, service: str
    ) -> TaskDefinition:
        return cls._taskDefinitions[cls._currentTaskARN(cluster, service)]


    @classmethod
    def _currentContainerDefinition(
        cls, cluster: str, service: str
    ) -> Mapping[str, Any]:
        return (
            cls._currentTaskDefinition(cluster, service)
            ["containerDefinitions"][0]
        )


    @classmethod
    def _currentImageName(cls, cluster: str, service: str) -> str:
        return cls._currentContainerDefinition(cluster, service)["image"]


    @classmethod
    def _currentEnvironment(
        cls, cluster: str, service: str
    ) -> TaskEnvironment:
        return ECSServiceClient._environmentFromJSON(
            cls._currentContainerDefinition(cluster, service)["environment"]
        )


    #
    # Instance attributes
    #

    _awsService: str = attrib()
    @_awsService.validator
    def _validate_service(self, attribute: Attribute, value: Any) -> None:
        assert value == "ecs"


    def describe_task_definition(
        self, taskDefinition: str
    ) -> Mapping[str, TaskDefinition]:
        return {
            "taskDefinition": self._taskDefinitions[taskDefinition]
        }


    def list_task_definitions(
        self, familyPrefix: str
    ) -> Mapping[str, Sequence[str]]:
        return {
            "taskDefinitionArns": list(
                t["taskDefinitionArn"]
                for t in self._taskDefinitions.values()
                if t["family"].startswith(familyPrefix)
            )
        }


    def register_task_definition(
        self, **taskDefinition: Any
    ) -> Mapping[str, TaskDefinition]:
        # Come up with a new task ARN
        maxVersion = 0
        for arn in self._taskDefinitions:
            version = int(arn.split(":")[-1])
            if version > maxVersion:
                maxVersion = version

        arn = (
            f"{self._defaultARNNamespace}:{maxVersion + 1}"
        )

        taskDefinition["taskDefinitionArn"] = arn
        taskDefinition["revision"] = maxVersion + 1
        taskDefinition["status"] = "ACTIVE"
        taskDefinition["compatibilities"] = self._defaultCompatibilities
        taskDefinition["requiresAttributes"] = self._defaultRequiresAttributes

        self._taskDefinitions[arn] = taskDefinition

        return {"taskDefinition": taskDefinition}


    def describe_services(
        self, cluster: str, services: Sequence[str]
    ) -> Mapping[str, Sequence[Mapping[str, str]]]:
        return {
            "services": [
                {"taskDefinition": self._currentTaskARN(cluster, service)}
                for service in services
            ],
        }


    def update_service(
        self, cluster: str, service: str, taskDefinition: str
    ) -> None:
        assert taskDefinition in self._taskDefinitions
        self._setCurrentTaskARN(cluster, service, taskDefinition)


class ECSServiceClientTests(TestCase):
    """
    Tests for :class:`ECSServiceClient`
    """

    def setUp(self) -> None:
        MockBoto3Client._addDefaultData()
        self.patch(ecs, "Boto3Client", MockBoto3Client)


    def tearDown(self) -> None:
        MockBoto3Client._clearData()


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
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )
        self.assertIsInstance(client._client, MockBoto3Client)


    def test_currentTaskARN(self) -> None:
        """
        :meth:`ECSServiceClient.currentTaskARN` returns the ARN of the current
        task.
        """
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )
        arn = client.currentTaskARN()
        self.assertEqual(
            arn, client._client._currentTaskARN(client.cluster, client.service)
        )


    def test_currentTaskDefinition(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )
        taskDefinition = client.currentTaskDefinition()
        self.assertIsInstance(taskDefinition, dict)
        self.assertTrue(taskDefinition.get("family"))
        self.assertTrue(taskDefinition.get("revision"))
        self.assertTrue(taskDefinition.get("containerDefinitions"))
        self.assertIn("FARGATE", taskDefinition.get("compatibilities", []))


    def test_currentImageName(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )
        imageName = client.currentImageName()
        self.assertEqual(
            imageName,
            client._client._currentImageName(client.cluster, client.service),
        )


    @given(integers(min_value=2))
    def test_updateTaskDefinition_updated(self, tag: int) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        repo, oldTag = client.currentImageName().split(":")
        assume(int(oldTag) != tag)
        newImageName = f"{repo}:{tag}"

        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        self.assertEqual(
            client._taskImageName(newTaskDefinition), newImageName
        )


    def test_updateTaskDefinition_none(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        self.assertRaises(NoChangesError, client.updateTaskDefinition)


    def test_updateTaskDefinition_same(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        self.assertRaises(
            NoChangesError,
            client.updateTaskDefinition, imageName=client.currentImageName()
        )


    @given(environment_updates(min_size=1))
    def test_updateTaskDefinition_updateEnvironment(
        self, newEnvironment: TaskEnvironment
    ) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

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
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

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
        expectedEnvironment = dict(
            client._client._currentEnvironment(client.cluster, client.service)
        )
        expectedEnvironment.update(travisEnvironment)
        del expectedEnvironment["TRAVIS"]

        # TASK_UPDATED is inserted during updates.
        self.assertIn("TASK_UPDATED", updatedEnvironment)
        expectedEnvironment["TASK_UPDATED"] = (
            updatedEnvironment["TASK_UPDATED"]
        )

        self.assertEqual(updatedEnvironment, expectedEnvironment)


    def test_registerTaskDefinition(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

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
            client._client._defaultCompatibilities
        )
        expectedTaskDefinition["requiresAttributes"] = (
            client._client._defaultRequiresAttributes
        )

        self.assertEqual(registeredTaskDefinition, expectedTaskDefinition)


    def test_currentTaskEnvironment(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        self.assertEqual(
            client.currentTaskEnvironment(),
            client._client._currentEnvironment(client.cluster, client.service),
        )


    @given(dictionaries(text(), text()))
    def test_updateTaskEnvironment_set(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        newEnvironment = client.updateTaskEnvironment(updates)
        expectedEnvironment = dict(
            client._client._currentEnvironment(client.cluster, client.service)
        )
        expectedEnvironment.update(updates)

        self.assertEqual(newEnvironment, expectedEnvironment)


    @given(set_unset_envs())
    def test_updateTaskEnvironment_unset(
        self, instructions: Tuple[TaskEnvironmentUpdates, Set[str]]
    ) -> None:
        updates, removes = instructions

        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

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
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        newImageName = f"{client.currentImageName()}1934"
        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        arn = client.registerTaskDefinition(newTaskDefinition)
        client.deployTask(arn)

        self.assertEqual(
            client._client._currentTaskARN(client.cluster, client.service), arn
        )


    def test_deployTaskDefinition(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        newImageName = f"{client.currentImageName()}9347"
        newTaskDefinition = client.updateTaskDefinition(imageName=newImageName)

        client.deployTaskDefinition(newTaskDefinition)

        newTaskDefinition = client.currentTaskDefinition()

        self.assertEqual(
            ECSServiceClient._taskImageName(newTaskDefinition), newImageName
        )


    def test_deployImage_new(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        newImageName = f"{client.currentImageName()}1046"

        client.deployImage(newImageName)

        newTaskDefinition = client.currentTaskDefinition()

        self.assertEqual(
            ECSServiceClient._taskImageName(newTaskDefinition), newImageName
        )


    def test_deployImage_same(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        expectedImageName = client.currentImageName()
        client.deployImage(expectedImageName)
        self.assertEqual(client.currentImageName(), expectedImageName)


    @given(environment_updates(min_size=1))
    def test_deployTaskEnvironment_updates(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        expectedEnvironment = dict(
            client._client._currentEnvironment(client.cluster, client.service)
        )
        expectedEnvironment.update(updates)

        client.deployTaskEnvironment(updates)
        newEnvironment = client._client._currentEnvironment(
            client.cluster, client.service
        )

        expectedEnvironment["TASK_UPDATED"] = newEnvironment["TASK_UPDATED"]

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_deployTaskEnvironment_none(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        expectedEnvironment = dict(
            client._client._currentEnvironment(client.cluster, client.service)
        )

        client.deployTaskEnvironment({})
        newEnvironment = client._client._currentEnvironment(
            client.cluster, client.service
        )

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_deployTaskEnvironment_same(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

        expectedEnvironment = dict(
            client._client._currentEnvironment(client.cluster, client.service)
        )

        client.deployTaskEnvironment(expectedEnvironment)
        newEnvironment = client._client._currentEnvironment(
            client.cluster, client.service
        )

        self.assertEqual(newEnvironment, expectedEnvironment)


    def test_rollback(self) -> None:
        client = ECSServiceClient(
            cluster=MockBoto3Client._sampleClusterStaging,
            service=MockBoto3Client._sampleServiceStaging,
        )

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
        MockBoto3Client._addDefaultData()
        self.patch(ecs, "Boto3Client", MockBoto3Client)

        # Patch ECSServiceClient constructor so we can track usage
        self.clients: List[ECSServiceClient] = []

        class RememberMeECSServiceClient(ECSServiceClient):
            def __init__(innerSelf, cluster: str, service: str) -> None:
                super().__init__(cluster=cluster, service=service)
                self.clients.append(innerSelf)

        self.patch(ecs, "ECSServiceClient", RememberMeECSServiceClient)


    def tearDown(self) -> None:
        self.cleanUp()


    def cleanUp(self) -> None:
        self.exitStatus.clear()
        self.clients.clear()
        MockBoto3Client._clearData()


    def initClusterAndService(
        self, cluster: str, service: str, taskARN: str = ""
    ) -> None:
        if not taskARN:
            taskARN = (
                MockBoto3Client._defaultTaskDefinitions[1]["taskDefinitionArn"]
            )
        MockBoto3Client._addDefaultTaskDefinitions()
        if cluster not in MockBoto3Client._services:
            MockBoto3Client._addCluster(cluster)
        MockBoto3Client._addService(cluster, service, taskARN)


    @given(text(min_size=1), text(min_size=1), text(min_size=1))
    def test_staging(
        self, stagingCluster: str, stagingService: str, imageName: str,
    ) -> None:
        # Because hypothesis and multiple runs
        self.cleanUp()

        # Add starting data set
        self.initClusterAndService(stagingCluster, stagingService)

        # Run "staging" subcommand
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
    def test_rollback(
        self, stagingCluster: str, stagingService: str
    ) -> None:
        # Because hypothesis and multiple runs
        self.cleanUp()

        # Add starting data set
        self.initClusterAndService(stagingCluster, stagingService)

        # Run "rollback" subcommand
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
            client.currentImageName(),
            (
                client._client._defaultTaskDefinitions[-2]
                ["containerDefinitions"][0]
                ["image"]
            )
        )


    @given(
        text(min_size=1), text(min_size=1), text(min_size=1), text(min_size=1)
    )
    def test_production(
        self,
        stagingCluster: str, stagingService: str,
        productionCluster: str, productionService: str,
    ) -> None:
        assume(
            (stagingCluster, stagingService) !=
            (productionCluster, productionService)
        )

        # Because hypothesis and multiple runs
        self.cleanUp()

        # Add starting data set
        self.initClusterAndService(stagingCluster, stagingService)
        self.initClusterAndService(
            productionCluster, productionService,
            MockBoto3Client._defaultTaskDefinitions[0]["taskDefinitionArn"],
        )

        # Run "production" subcommand
        self.patch(sys, "argv", [
            "deploy_aws", "production",
            "--staging-cluster", stagingCluster,
            "--staging-service", stagingService,
            "--production-cluster", productionCluster,
            "--production-service", productionService,
        ])
        ECSServiceClient.main()

        self.assertEqual(self.exitStatus, [0])
        self.assertEqual(len(self.clients), 2)

        stagingClient, productionClient = self.clients

        self.assertEqual(stagingClient.cluster, stagingCluster)
        self.assertEqual(stagingClient.service, stagingService)
        self.assertEqual(productionClient.cluster, productionCluster)
        self.assertEqual(productionClient.service, productionService)
        self.assertEqual(
            stagingClient.currentImageName(),
            productionClient.currentImageName(),
        )
