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

from contextlib import contextmanager
from copy import deepcopy
from os import chdir, environ, getcwd
from os.path import dirname
from typing import (
    Any,
    Callable,
    ClassVar,
    ContextManager,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    cast,
)

from attr import Attribute, attrib, attrs

from hypothesis import assume, given, settings
from hypothesis.strategies import (
    booleans,
    characters,
    composite,
    dictionaries,
    integers,
    just,
    lists,
    one_of,
    sampled_from,
    sets,
    text,
    tuples,
)

from twisted.trial.unittest import SynchronousTestCase as TestCase

import deploy.notify.smtp
from deploy.ext.click import clickTestRun
from deploy.ext.hypothesis import (
    ascii_text,
    aws_resource_names,
    commitIDs,
    email_addresses,
    image_names,
    image_repository_names,
    port_numbers,
    repository_ids,
    user_names,
)
from deploy.notify.smtp import SMTPNotifier

from .test_ecr import ECRServiceClient, testingECRServiceClient
from .. import ecs
from ..ecs import (
    ECSCluster,
    ECSService,
    ECSServiceClient,
    ECSTaskDefinition,
    NoChangesError,
    NoSuchServiceError,
    TaskDefinitionJSON,
    TaskEnvironment,
    TaskEnvironmentUpdates,
)


__all__ = ()


def environment_updates(
    min_size: int = 0, max_size: Optional[int] = None
) -> Mapping[str, str]:
    return cast(
        Mapping[str, str],
        dictionaries(
            text(min_size=1), text(), min_size=min_size, max_size=max_size,
        ),
    )


@composite
def set_unset_envs(draw: Callable) -> Tuple[Dict[str, str], Set[str]]:
    updates = draw(environment_updates(min_size=1))
    removes = draw(sets(elements=just(tuple(updates.keys()))))
    return (updates, removes)


@attrs(auto_attribs=True)
class MockBoto3ECSClient(object):
    """
    Mock Boto3 client.
    """

    #
    # Class attributes
    #

    _sampleClusterStaging: ClassVar = "staging-cluster"
    _sampleServiceStaging: ClassVar = "staging-service-fg"
    _sampleClusterProduction: ClassVar = "production-cluster"
    _sampleServiceProduction: ClassVar = "production-service-fg"

    _defaultARNNamespace: ClassVar = "arn:mock:task-definition/service"

    _defaultCompatibilities: ClassVar[Sequence[str]] = ["EC2", "FARGATE"]

    _defaultRequiresAttributes: ClassVar[Sequence[Mapping[str, str]]] = [
        {"name": "ecs.capability.execution-role-ecr-pull"},
        {"name": "com.amazonaws.ecs.capability.ecr-auth"},
        {"name": "com.amazonaws.ecs.capability.task-iam-role"},
    ]

    _defaultTaskDefinitions: ClassVar[Sequence[TaskDefinitionJSON]] = [
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
                    "environment": ECSTaskDefinition._environmentAsJSON(
                        {"version": "0", "happiness": "true",}
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
                    "environment": ECSTaskDefinition._environmentAsJSON(
                        {
                            "version": "0",
                            "happiness": "true",
                            "VARIABLE1": "value1",
                            "VARIABLE2": "value2",
                        }
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
            "requiresAttributes": deepcopy(_defaultRequiresAttributes),
            "placementConstraints": [],
            "compatibilities": list(_defaultCompatibilities),
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "256",
            "memory": "512",
        },
    ]

    _taskDefinitions: ClassVar[Dict[str, TaskDefinitionJSON]] = {}
    _services: ClassVar[Dict[str, Dict[str, str]]] = {}

    @classmethod
    def _addDefaultTaskDefinitions(cls) -> None:
        for taskDefinition in cls._defaultTaskDefinitions:
            cls._taskDefinitions[
                taskDefinition["taskDefinitionArn"]
            ] = deepcopy(taskDefinition)

    @classmethod
    def _defaultTaskARNs(cls) -> Sequence[str]:
        return [
            taskDefinition["taskDefinitionArn"]
            for taskDefinition in cls._defaultTaskDefinitions
        ]

    @classmethod
    def _clearTaskDefinitions(cls) -> None:
        cls._taskDefinitions.clear()

    @classmethod
    def _addCluster(cls, cluster: str) -> None:
        if cluster in cls._services:  # pragma: no cover
            raise AssertionError(f"Cluster {cluster!r} already exists")
        cls._services[cluster] = {}

    @classmethod
    def _addService(cls, cluster: str, service: str, arn: str) -> None:
        if service in cls._services[cluster]:  # pragma: no cover
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
            cls._defaultTaskARNs()[-1],
        )

        cls._addCluster(cls._sampleClusterProduction)
        cls._addService(
            cls._sampleClusterProduction,
            cls._sampleServiceProduction,
            cls._defaultTaskARNs()[-1],
        )

    @classmethod
    def _clearServices(cls) -> None:
        cls._services.clear()

    @classmethod
    def _currentTaskARN(cls, cluster: str, service: str) -> str:
        if cluster not in cls._services:  # pragma: no cover
            raise AssertionError(
                f"Cluster {cluster!r} not in {cls._services.keys()}"
            )
        if service not in cls._services[cluster]:  # pragma: no cover
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
    ) -> TaskDefinitionJSON:
        return cls._taskDefinitions[cls._currentTaskARN(cluster, service)]

    @classmethod
    def _currentContainerDefinition(
        cls, cluster: str, service: str
    ) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            (
                cls._currentTaskDefinition(cluster, service)[
                    "containerDefinitions"
                ][0]
            ),
        )

    @classmethod
    def _currentImageName(cls, cluster: str, service: str) -> str:
        return cast(
            str, cls._currentContainerDefinition(cluster, service)["image"]
        )

    @classmethod
    def _currentEnvironment(cls, cluster: str, service: str) -> TaskEnvironment:
        return ECSTaskDefinition._environmentFromJSON(
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
    ) -> Mapping[str, TaskDefinitionJSON]:
        return {"taskDefinition": self._taskDefinitions[taskDefinition]}

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
    ) -> Mapping[str, TaskDefinitionJSON]:
        # Come up with a new task ARN
        maxVersion = 0
        for arn in self._taskDefinitions:
            version = int(arn.split(":")[-1])
            if version > maxVersion:
                maxVersion = version

        arn = f"{self._defaultARNNamespace}:{maxVersion + 1}"

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
                if service in self._services[cluster]
            ],
        }

    def update_service(
        self, cluster: str, service: str, taskDefinition: str
    ) -> None:
        assert taskDefinition in self._taskDefinitions
        self._setCurrentTaskARN(cluster, service, taskDefinition)


@contextmanager
def testingBoto3ECS() -> Iterator[None]:
    MockBoto3ECSClient._addDefaultTaskDefinitions()
    MockBoto3ECSClient._addDefaultServices()

    boto3Client = ecs.boto3Client
    ecs.boto3Client = MockBoto3ECSClient

    try:
        yield

    finally:
        ecs.boto3Client = boto3Client

        MockBoto3ECSClient._clearServices()
        MockBoto3ECSClient._clearTaskDefinitions()


class ECSTaskDefinitionTests(TestCase):
    """
    Tests for :class:`ECSTaskDefinition`
    """

    def taskDefinition(self) -> ECSTaskDefinition:
        cluster = ECSCluster(name=MockBoto3ECSClient._sampleClusterStaging)
        service = ECSService(
            cluster=cluster, name=MockBoto3ECSClient._sampleServiceStaging,
        )
        client = ECSServiceClient(service=service)
        return client.currentTaskDefinition(service)

    def test_environmentFromJSON(self) -> None:
        self.assertEqual(
            ECSTaskDefinition._environmentFromJSON(
                [{"name": "foo", "value": "bar"}, {"name": "x", "value": "1"},]
            ),
            {"foo": "bar", "x": "1"},
        )

    def test_environmentAsJSON(self) -> None:
        self.assertEqual(
            ECSTaskDefinition._environmentAsJSON({"foo": "bar", "x": "1"}),
            [{"name": "foo", "value": "bar"}, {"name": "x", "value": "1"}],
        )

    def test_taskImageName(self) -> None:
        name = "xyzzy"
        self.assertEqual(
            ECSTaskDefinition._taskImageName(
                {"containerDefinitions": [{"image": name}]}
            ),
            name,
        )

    @given(integers(min_value=2))
    def test_update_updated(self, tag: int) -> None:
        with testingBoto3ECS():
            original = self.taskDefinition()

            repo, oldTag = original.imageName.split(":")
            assume(int(oldTag) != tag)
            newImageName = f"{repo}:{tag}"

            updated = original.update(imageName=newImageName)

            self.assertEqual(updated.imageName, newImageName)

    def test_update_none(self) -> None:
        with testingBoto3ECS():
            taskDefinition = self.taskDefinition()

            self.assertRaises(NoChangesError, taskDefinition.update)

    def test_update_same(self) -> None:
        with testingBoto3ECS():
            taskDefinition = self.taskDefinition()

            self.assertRaises(
                NoChangesError,
                taskDefinition.update,
                imageName=taskDefinition.imageName,
            )

    @given(environment_updates(min_size=1))
    def test_update_updateEnvironment(
        self, newEnvironment: TaskEnvironment
    ) -> None:
        with testingBoto3ECS():
            original = self.taskDefinition()

            # TRAVIS environment variable makes Travis-CI things happen which
            # we aren't testing for here.
            assume("TRAVIS" not in newEnvironment)

            updated = original.update(environment=newEnvironment)
            updatedEnvironment = dict(updated.environment)
            expectedEnvironment = dict(newEnvironment)

            # TASK_UPDATED is inserted during updates.
            self.assertIn("TASK_UPDATED", updatedEnvironment)
            expectedEnvironment["TASK_UPDATED"] = updatedEnvironment[
                "TASK_UPDATED"
            ]

            self.assertEqual(updatedEnvironment, expectedEnvironment)

    @given(
        ascii_text(min_size=1),  # project
        repository_ids(),  # repository
        integers(),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        commitIDs(),  # commitID
        ascii_text(min_size=1),  # commitMessage
    )
    def test_update_ci(
        self,
        project: str,
        repository: str,
        buildNumber: int,
        buildURL: str,
        commitID: str,
        commitMessage: str,
    ) -> None:
        with testingBoto3ECS():
            original = self.taskDefinition()

            ciEnvironment = {
                "BUILD_NUMBER": str(buildNumber),
                "BUILD_URL": buildURL,
                "COMMIT_ID": "0" * 40,
                "COMMIT_MESSAGE": commitMessage,
                "PROJECT_NAME": project,
                "REPOSITORY_ID": repository,
            }

            expectedEnvironment = dict(original.environment)
            expectedEnvironment.update(
                {(f"CI_{k}", v) for (k, v) in ciEnvironment.items()}
            )

            # Patch the (local) system environment to emulate CI
            self.patch(ecs, "environ", ciEnvironment)

            # Make an unrelated change to avoid NoChangesError
            updated = original.update(imageName=f"{original.imageName}4027")
            updatedEnvironment = dict(updated.environment)

            # TASK_UPDATED is inserted during updates.
            self.assertIn("TASK_UPDATED", updatedEnvironment)
            expectedEnvironment["TASK_UPDATED"] = updatedEnvironment[
                "TASK_UPDATED"
            ]

            self.assertEqual(updatedEnvironment, expectedEnvironment)


class ECSServiceClientTests(TestCase):
    """
    Tests for :class:`ECSServiceClient`
    """

    def stagingCluster(self) -> ECSCluster:
        return ECSCluster(name=MockBoto3ECSClient._sampleClusterStaging)

    def stagingService(self) -> ECSService:
        return ECSService(
            cluster=self.stagingCluster(),
            name=MockBoto3ECSClient._sampleServiceStaging,
        )

    def stagingClient(self) -> ECSServiceClient:
        return ECSServiceClient(service=self.stagingService())

    def test_aws(self) -> None:
        """
        :meth:`ECSServiceClient._aws` property returns an AWS client.
        """
        with testingBoto3ECS():
            client = self.stagingClient()

            self.assertIsInstance(client._aws, MockBoto3ECSClient)

    def test_lookupTaskARN(self) -> None:
        """
        :meth:`ECSServiceClient._lookupTaskARN` returns the ARN of the given
        task.
        """
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            self.assertEqual(
                client._lookupTaskARN(service),
                client._aws._currentTaskARN(service.cluster.name, service.name),
            )

    def test_lookupTaskARN_noSuchService(self) -> None:
        """
        :meth:`ECSServiceClient._lookupTaskARN` raises
        :exc:`NoSuchServiceError` when the service doesn't exist.
        task.
        """
        with testingBoto3ECS():
            doesntExistServiceName = "xyzzy"
            service = ECSService(
                cluster=self.stagingCluster(), name=doesntExistServiceName
            )
            client = ECSServiceClient(service=service)

            e = self.assertRaises(
                NoSuchServiceError, client._lookupTaskARN, service
            )
            self.assertEqual(e.service.name, doesntExistServiceName)

    def test_lookupTaskDefinition(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            arn = client._lookupTaskARN(service)
            taskDefinition = client._lookupTaskDefinition(arn)
            json = taskDefinition.json

            self.assertIsInstance(json, dict)
            self.assertTrue(json.get("family"))
            self.assertTrue(json.get("revision"))
            self.assertTrue(json.get("containerDefinitions"))
            self.assertIn("FARGATE", json.get("compatibilities", []))

    def test_currentTaskDefinition(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)
            arn = taskDefinition.arn

            assert arn is not None

            self.assertEqual(arn, client._lookupTaskARN(service))
            self.assertEqual(taskDefinition, client._lookupTaskDefinition(arn))

    def test_currentTaskDefinition_cached(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            self.assertIdentical(
                client.currentTaskDefinition(service), taskDefinition
            )

    def test_registerTaskDefinition(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            repo, tag = taskDefinition.imageName.split(":")
            newImageName = f"{repo}:{tag}1987"

            newTaskDefinition = taskDefinition.update(imageName=newImageName)

            arn = client.registerTaskDefinition(newTaskDefinition)
            registeredTaskDefinition = client._aws.describe_task_definition(
                arn
            )["taskDefinition"]

            expectedTaskDefinition = dict(newTaskDefinition.json)
            expectedTaskDefinition["taskDefinitionArn"] = arn
            expectedTaskDefinition["revision"] = int(arn.split(":")[-1])
            expectedTaskDefinition["status"] = "ACTIVE"
            expectedTaskDefinition[
                "compatibilities"
            ] = client._aws._defaultCompatibilities
            expectedTaskDefinition[
                "requiresAttributes"
            ] = client._aws._defaultRequiresAttributes

            self.assertEqual(registeredTaskDefinition, expectedTaskDefinition)

    def test_deployTaskWithARN(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            newImageName = f"{taskDefinition.imageName}1934"
            newTaskDefinition = taskDefinition.update(imageName=newImageName)

            arn = client.registerTaskDefinition(newTaskDefinition)
            client.deployTaskWithARN(service, arn)

            self.assertEqual(
                client._aws._currentTaskARN(service.cluster.name, service.name),
                arn,
            )

    def test_deployTaskDefinition(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            newImageName = f"{taskDefinition.imageName}9347"
            newTaskDefinition = taskDefinition.update(imageName=newImageName)

            client.deployTaskDefinition(service, newTaskDefinition)
            newTask = client.currentTaskDefinition(service)

            self.assertEqual(newTask.imageName, newImageName)

    def test_deployImage_new(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            newImageName = f"{taskDefinition.imageName}1046"

            client.deployImage(service, newImageName)
            newTask = client.currentTaskDefinition(service)

            self.assertEqual(newTask.imageName, newImageName)

    def test_deployImage_same(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            expectedImageName = taskDefinition.imageName
            client.deployImage(service, expectedImageName)
            self.assertEqual(taskDefinition.imageName, expectedImageName)

    @given(dictionaries(text(), text()))
    def test_updateTaskEnvironment_set(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            newEnvironment = client.updateTaskEnvironment(service, updates)
            expectedEnvironment = dict(
                client._aws._currentEnvironment(
                    service.cluster.name, service.name
                )
            )
            expectedEnvironment.update(updates)

            self.assertEqual(newEnvironment, expectedEnvironment)

    @given(set_unset_envs())
    def test_updateTaskEnvironment_unset(
        self, instructions: Tuple[TaskEnvironmentUpdates, Set[str]]
    ) -> None:
        with testingBoto3ECS():
            updates, removes = instructions

            client = self.stagingClient()
            service = client.service

            expectedEnvironment = dict(
                client.currentTaskDefinition(service).environment
            )
            for key, value in updates.items():  # pragma: no cover
                if key in removes:
                    if key in expectedEnvironment:
                        del expectedEnvironment[key]
                else:
                    assert value is not None
                    expectedEnvironment[key] = value

            # Deploy the input updates and get back the result
            client.deployTaskEnvironment(service, updates)
            newEnvironment = client.updateTaskEnvironment(
                service, {k: None for k in removes}
            )

            expectedEnvironment["TASK_UPDATED"] = newEnvironment["TASK_UPDATED"]

            self.assertEqual(newEnvironment, expectedEnvironment)

    @given(environment_updates(min_size=1))
    def test_deployTaskEnvironment_updates(
        self, updates: TaskEnvironmentUpdates
    ) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            expectedEnvironment = dict(
                client._aws._currentEnvironment(
                    service.cluster.name, service.name
                )
            )
            expectedEnvironment.update(updates)

            client.deployTaskEnvironment(service, updates)
            newEnvironment = client._aws._currentEnvironment(
                service.cluster.name, service.name
            )

            expectedEnvironment["TASK_UPDATED"] = newEnvironment["TASK_UPDATED"]

            self.assertEqual(newEnvironment, expectedEnvironment)

    def test_deployTaskEnvironment_none(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            expectedEnvironment = dict(
                client._aws._currentEnvironment(
                    service.cluster.name, service.name
                )
            )

            client.deployTaskEnvironment(service, {})
            newEnvironment = client._aws._currentEnvironment(
                service.cluster.name, service.name
            )

            self.assertEqual(newEnvironment, expectedEnvironment)

    def test_deployTaskEnvironment_same(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service

            expectedEnvironment = dict(
                client._aws._currentEnvironment(
                    service.cluster.name, service.name
                )
            )

            client.deployTaskEnvironment(service, expectedEnvironment)
            newEnvironment = client._aws._currentEnvironment(
                service.cluster.name, service.name
            )

            self.assertEqual(newEnvironment, expectedEnvironment)

    def test_rollback(self) -> None:
        with testingBoto3ECS():
            client = self.stagingClient()
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            expectedImageName = taskDefinition.imageName
            newImageName = f"{expectedImageName}2957"

            client.deployImage(service, newImageName)
            client.rollback(service)

            self.assertEqual(taskDefinition.imageName, expectedImageName)


@contextmanager
def testingECSServiceClient() -> Iterator[List[ECSServiceClient]]:
    clients: List[ECSServiceClient] = []

    class RememberMeECSServiceClient(ECSServiceClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            clients.append(self)

    Client = ecs.ECSServiceClient
    ecs.ECSServiceClient = cast(  # type: ignore[misc]
        Type, RememberMeECSServiceClient
    )

    try:
        with testingBoto3ECS():
            yield clients

    finally:
        ecs.ECSServiceClient = Client  # type: ignore[misc]

        clients.clear()


def ciWorkingDirectory(env: Mapping[str, str]) -> str:
    if "TOX_WORK_DIR" in env:  # pragma: no cover
        return dirname(env["TOX_WORK_DIR"])
    else:  # pragma: no cover
        return dirname(dirname(dirname(dirname(dirname(__file__)))))


@contextmanager
def notCIEnvironment() -> Iterator[None]:
    env = environ.copy()

    for key in ("CI", "TRAVIS"):
        environ.pop(key, None)

    wd = getcwd()
    chdir(ciWorkingDirectory(env))

    try:
        yield

    finally:
        environ.clear()
        environ.update(env)
        chdir(wd)


@contextmanager
def ciEnvironment() -> Iterator[None]:
    env = environ.copy()
    environ["CI"] = "true"

    wd = getcwd()
    chdir(ciWorkingDirectory(env))

    try:
        yield

    finally:
        environ.clear()
        environ.update(env)
        chdir(wd)


@contextmanager
def travisEnvironment(omit: str = "") -> Iterator[None]:
    env = environ.copy()

    environ["TRAVIS"] = "true"
    if omit != "pr":
        environ["TRAVIS_PULL_REQUEST"] = "false"
    if omit != "branch":
        environ["TRAVIS_BRANCH"] = "master"

    wd = getcwd()
    chdir(ciWorkingDirectory(env))

    try:
        yield

    finally:
        environ.clear()
        environ.update(env)
        chdir(wd)


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class MockSMTPNotifier(SMTPNotifier):
    _notifyStagingCalls: ClassVar[List[Mapping[str, Any]]] = []

    def notifyStaging(
        self,
        project: str,
        repository: str,
        buildNumber: str,
        buildURL: str,
        commitID: str,
        commitMessage: str,
        trialRun: bool,
    ) -> None:
        self._notifyStagingCalls.append(
            dict(
                project=project,
                repository=repository,
                buildNumber=buildNumber,
                buildURL=buildURL,
                commitID=commitID,
                commitMessage=commitMessage,
                trialRun=trialRun,
            )
        )


@contextmanager
def testingSMTPNotifier() -> Iterator[None]:
    SMTPNotifier = deploy.notify.smtp.SMTPNotifier
    deploy.notify.smtp.SMTPNotifier = (  # type: ignore[misc]
        MockSMTPNotifier
    )

    try:
        yield None

    finally:
        deploy.notify.smtp.SMTPNotifier = (  # type: ignore[misc]
            SMTPNotifier
        )
        MockSMTPNotifier._notifyStagingCalls.clear()


class CommandLineTests(TestCase):
    """
    Tests for the :class:`ECSServiceClient` command line.
    """

    def initClusterAndService(
        self, cluster: str, service: str, taskARN: str = ""
    ) -> None:
        if not taskARN:
            taskARN = MockBoto3ECSClient._defaultTaskARNs()[1]
        MockBoto3ECSClient._addDefaultTaskDefinitions()
        if cluster not in MockBoto3ECSClient._services:
            MockBoto3ECSClient._addCluster(cluster)
        MockBoto3ECSClient._addService(cluster, service, taskARN)

    def _test_staging(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
        environment: Callable[[], ContextManager] = ciEnvironment,
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "staging" subcommand
            with environment():
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        stagingServiceName,
                        "--image-ecr",
                        ecrImageName,
                    ],
                )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            self.assertEqual(service.cluster.name, stagingClusterName)
            self.assertEqual(service.name, stagingServiceName)

            if ":" in ecrImageName:
                self.assertEqual(taskDefinition.imageName, ecrImageName)
            else:
                self.assertTrue(
                    taskDefinition.imageName.startswith(ecrImageName)
                )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_ci(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        self._test_staging(
            stagingClusterName,
            stagingServiceName,
            ecrImageName,
            environment=ciEnvironment,
        )

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_travis(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        self._test_staging(
            stagingClusterName,
            stagingServiceName,
            ecrImageName,
            environment=travisEnvironment,
        )

    @given(aws_resource_names(), aws_resource_names(), image_repository_names())
    @settings(max_examples=5)
    def test_staging_noECRImageTag(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        self._test_staging(stagingClusterName, stagingServiceName, ecrImageName)

    @given(
        aws_resource_names(),
        aws_resource_names(),
        one_of(image_names(), image_repository_names()),
    )
    @settings(max_examples=50)
    def test_staging_push(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        with testingECRServiceClient():
            with testingECSServiceClient() as clients:
                # Add starting data set
                self.initClusterAndService(
                    stagingClusterName, stagingServiceName
                )

                # Run "staging" subcommand
                with travisEnvironment():
                    result = clickTestRun(
                        ECSServiceClient.main,
                        [
                            "deploy_aws_ecs",
                            "staging",
                            "--staging-cluster",
                            stagingClusterName,
                            "--staging-service",
                            stagingServiceName,
                            "--image-ecr",
                            ecrImageName,
                            "--image-local",
                            "image:1",  # FIXME: static
                        ],
                    )

                self.assertEqual(len(clients), 1)
                ecsClient = clients[0]
                ecsService = ecsClient.service

                self.assertEqual(
                    ecsClient.service.cluster.name, stagingClusterName
                )
                self.assertEqual(ecsClient.service.name, stagingServiceName)

                currentImageName = ecsClient.currentTaskDefinition(
                    ecsService
                ).imageName
                if ":" in ecrImageName:
                    self.assertEqual(currentImageName, ecrImageName)
                else:
                    self.assertTrue(
                        currentImageName.startswith(f"{ecrImageName}:")
                    )
                    ecrImageName = currentImageName

                # ECR tag should exist both locally and in ECR
                ecrClient = ECRServiceClient()
                self.assertIsNotNone(ecrClient.imageWithName(currentImageName))
                self.assertIsNotNone(
                    ecrClient._docker.images._fromECR(currentImageName)
                )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(
        aws_resource_names(),  # stagingClusterName
        aws_resource_names(),  # stagingServiceName
        one_of(image_names(), image_repository_names()),  # ecrImageName
        ascii_text(min_size=1),  # smtpHost
        port_numbers(),  # smtpPort
        user_names(),  # smtpUser
        ascii_text(min_size=1),  # smtpPassword
        email_addresses(),  # senderAddress
        email_addresses(),  # recipientAddress
        ascii_text(min_size=1),  # project
        repository_ids(),  # repository
        ascii_text(min_size=1),  # buildNumber
        ascii_text(min_size=1),  # buildURL
        commitIDs(),  # commitID
        ascii_text(min_size=1),  # commitMessage  FIXME: use text()
        booleans(),  # trialRun
    )
    def test_staging_notify(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
        smtpHost: str,
        smtpPort: int,
        smtpUser: str,
        smtpPassword: str,
        senderAddress: str,
        recipientAddress: str,
        project: str,
        repository: str,
        buildNumber: str,
        buildURL: str,
        commitID: str,
        commitMessage: str,
        trialRun: bool,
    ) -> None:
        args = [
            "deploy_aws_ecs",
            "staging",
            "--staging-cluster",
            stagingClusterName,
            "--staging-service",
            stagingServiceName,
            "--image-ecr",
            ecrImageName,
            "--project-name",
            project,
            "--repository-id",
            repository,
            "--build-number",
            buildNumber,
            "--build-url",
            buildURL,
            "--commit-id",
            commitID,
            "--commit-message",
            commitMessage,
            "--smtp-host",
            smtpHost,
            "--smtp-port",
            str(smtpPort),
            "--smtp-user",
            smtpUser,
            "--smtp-password",
            smtpPassword,
            "--email-sender",
            senderAddress,
            "--email-recipient",
            recipientAddress,
        ]

        if trialRun:
            args.append("--trial-run")

        with testingECSServiceClient():
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "staging" subcommand
            with travisEnvironment(), testingSMTPNotifier():
                result = clickTestRun(ECSServiceClient.main, args)

                self.assertEqual(
                    MockSMTPNotifier._notifyStagingCalls,
                    [
                        dict(
                            project=project,
                            repository=repository,
                            buildNumber=buildNumber,
                            buildURL=buildURL,
                            commitID=commitID,
                            commitMessage=commitMessage,
                            trialRun=trialRun,
                        )
                    ],
                )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_trial(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            startingImageName = MockBoto3ECSClient._currentImageName(
                cluster=stagingClusterName, service=stagingServiceName
            )

            # Run "staging" subcommand
            with travisEnvironment():
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        stagingServiceName,
                        "--image-ecr",
                        ecrImageName,
                        "--trial-run",
                    ],
                )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            self.assertEqual(service.cluster.name, stagingClusterName)
            self.assertEqual(service.name, stagingServiceName)
            self.assertEqual(taskDefinition.imageName, startingImageName)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_noSuchService(
        self, stagingClusterName: str, ecrImageName: str
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, "service")

            doesntExistServiceName = "xyzzy"

            # Run "staging" subcommand
            with travisEnvironment():
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        doesntExistServiceName,
                        "--image-ecr",
                        ecrImageName,
                    ],
                )

            self.assertEqual(len(clients), 1)

        errorMessage = result.stderr.getvalue()

        self.assertEqual(result.exitCode, 2)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertTrue(
            errorMessage.endswith(
                f"\n\nError: Unknown service: "
                f"{stagingClusterName}:{doesntExistServiceName}\n"
            ),
            errorMessage,
        )

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_notCI(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "staging" subcommand
            with notCIEnvironment():
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        stagingServiceName,
                        "--image-ecr",
                        ecrImageName,
                    ],
                )

            self.assertEqual(len(clients), 0)

        self.assertEqual(result.exitCode, 2)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertTrue(
            result.stderr.getvalue().endswith(
                "\n\nError: Deployment not allowed outside of CI environment\n"
            )
        )

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_travis_notPR(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "staging" subcommand
            with travisEnvironment(omit="pr"):
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        stagingServiceName,
                        "--image-ecr",
                        ecrImageName,
                    ],
                )

            self.assertEqual(len(clients), 0)

        self.assertEqual(result.exitCode, 2)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertTrue(
            result.stderr.getvalue().endswith(
                "\n\nError: Deployment not allowed from pull request\n"
            )
        )

    @given(aws_resource_names(), aws_resource_names(), image_names())
    @settings(max_examples=5)
    def test_staging_travis_notBranch(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        ecrImageName: str,
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "staging" subcommand
            with travisEnvironment(omit="branch"):
                result = clickTestRun(
                    ECSServiceClient.main,
                    [
                        "deploy_aws_ecs",
                        "staging",
                        "--staging-cluster",
                        stagingClusterName,
                        "--staging-service",
                        stagingServiceName,
                        "--image-ecr",
                        ecrImageName,
                    ],
                )

            self.assertEqual(len(clients), 0)

        self.assertEqual(result.exitCode, 2)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertTrue(
            result.stderr.getvalue().endswith(
                "\n\n"
                "Error: Deployment not allowed from branch None "
                "(must be 'master')\n"
            )
        )

    @given(aws_resource_names(), aws_resource_names())
    @settings(max_examples=5)
    def test_rollback(
        self, stagingClusterName: str, stagingServiceName: str
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)

            # Run "rollback" subcommand
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "rollback",
                    "--staging-cluster",
                    stagingClusterName,
                    "--staging-service",
                    stagingServiceName,
                ],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service
            taskDefinition = client.currentTaskDefinition(service)

            self.assertEqual(service.cluster.name, stagingClusterName)
            self.assertEqual(service.name, stagingServiceName)
            self.assertEqual(
                taskDefinition.imageName,
                (
                    client._aws._defaultTaskDefinitions[-2][
                        "containerDefinitions"
                    ][0]["image"]
                ),
            )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(
        aws_resource_names(),
        aws_resource_names(),
        aws_resource_names(),
        aws_resource_names(),
    )
    @settings(max_examples=5)
    def test_production(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        productionClusterName: str,
        productionServiceName: str,
    ) -> None:
        assume(
            (stagingClusterName, stagingServiceName)
            != (productionClusterName, productionServiceName)
        )

        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)
            self.initClusterAndService(
                productionClusterName,
                productionServiceName,
                MockBoto3ECSClient._defaultTaskARNs()[0],
            )

            # Run "production" subcommand
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "production",
                    "--staging-cluster",
                    stagingClusterName,
                    "--staging-service",
                    stagingServiceName,
                    "--production-cluster",
                    productionClusterName,
                    "--production-service",
                    productionServiceName,
                ],
            )

            self.assertEqual(len(clients), 2)
            stagingClient, productionClient = clients
            stagingService = stagingClient.service
            productionService = productionClient.service

            self.assertEqual(stagingService.cluster.name, stagingClusterName)
            self.assertEqual(stagingService.name, stagingServiceName)
            self.assertEqual(
                productionService.cluster.name, productionClusterName
            )
            self.assertEqual(productionService.name, productionServiceName)

            stagingTaskDefinition = stagingClient.currentTaskDefinition(
                stagingService
            )
            productionTaskDefinition = productionClient.currentTaskDefinition(
                productionService
            )

            self.assertEqual(
                stagingTaskDefinition.imageName,
                productionTaskDefinition.imageName,
            )

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(result.echoOutput, [])
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(
        aws_resource_names(),
        aws_resource_names(),
        aws_resource_names(),
        aws_resource_names(),
    )
    @settings(max_examples=5)
    def test_compare(
        self,
        stagingClusterName: str,
        stagingServiceName: str,
        productionClusterName: str,
        productionServiceName: str,
    ) -> None:
        assume(
            (stagingClusterName, stagingServiceName)
            != (productionClusterName, productionServiceName)
        )

        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(stagingClusterName, stagingServiceName)
            self.initClusterAndService(
                productionClusterName,
                productionServiceName,
                MockBoto3ECSClient._defaultTaskARNs()[0],
            )

            # Run "compare" subcommand
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "compare",
                    "--staging-cluster",
                    stagingClusterName,
                    "--staging-service",
                    stagingServiceName,
                    "--production-cluster",
                    productionClusterName,
                    "--production-service",
                    productionServiceName,
                ],
            )

            self.assertEqual(len(clients), 2)
            stagingClient, productionClient = clients
            stagingService = stagingClient.service
            productionService = productionClient.service

            self.assertEqual(stagingService.cluster.name, stagingClusterName)
            self.assertEqual(stagingService.name, stagingServiceName)
            self.assertEqual(
                productionService.cluster.name, productionClusterName
            )
            self.assertEqual(productionService.name, productionServiceName)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(
            result.echoOutput,
            [
                ("Staging task ARN: arn:mock:task-definition/service:1", {}),
                ("Staging container image: /team/service-project:1001", {}),
                ("Producton task ARN: arn:mock:task-definition/service:0", {}),
                ("Producton container image: /team/service-project:1000", {}),
                ("Matching environment variables:", {}),
                ("    happiness = 'true'", {}),
                ("    version = '0'", {}),
                ("Mismatching environment variables:", {}),
                ("    VARIABLE1 = 'value1' / None", {}),
                ("    VARIABLE2 = 'value2' / None", {}),
            ],
        )
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(aws_resource_names(), aws_resource_names())
    @settings(max_examples=5)
    def test_environment_get(self, clusterName: str, serviceName: str) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(clusterName, serviceName)

            # Run "environment" subcommand
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "environment",
                    "--cluster",
                    clusterName,
                    "--service",
                    serviceName,
                ],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service

            self.assertEqual(service.cluster.name, clusterName)
            self.assertEqual(service.name, serviceName)

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(
            result.echoOutput,
            [
                (f"Environment variables for {clusterName}:{serviceName}:", {}),
                (f"    version = '0'", {}),
                (f"    happiness = 'true'", {}),
                (f"    VARIABLE1 = 'value1'", {}),
                (f"    VARIABLE2 = 'value2'", {}),
            ],
        )
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

    @given(
        aws_resource_names(),
        aws_resource_names(),
        lists(  # updates
            tuples(
                text(  # updates/keys
                    alphabet=characters(
                        blacklist_categories=("Cs",), blacklist_characters="=",
                    ),
                ),
                text(),  # updates/values
            ),
            min_size=1,
        ),
    )
    @settings(max_examples=50)
    def test_environment_set(
        self, clusterName: str, serviceName: str, updates: List[Tuple[str, str]]
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(clusterName, serviceName)

            # Run "environment" subcommand
            # Prefix variable names with "x" to make sure they start with a
            # letter
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "environment",
                    "--cluster",
                    clusterName,
                    "--service",
                    serviceName,
                    # a letter
                    *[f"x{k}={v}" for k, v in updates],
                ],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service

            self.assertEqual(service.cluster.name, clusterName)
            self.assertEqual(service.name, serviceName)

            resultEnvironment = client.currentTaskDefinition(
                service
            ).environment

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(
            result.echoOutput,
            [
                (
                    f"Changing environment variables for "
                    f"{clusterName}:{serviceName}:",
                    {},
                )
            ]
            + [(f"    Setting x{k}.", {}) for k, v in updates],
        )
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

        for key, value in dict(updates).items():
            key = f"x{key}"
            self.assertIn(key, resultEnvironment)
            self.assertEqual(resultEnvironment[key], value)

    @given(
        aws_resource_names(),
        aws_resource_names(),
        lists(
            sampled_from(
                sorted(
                    ECSTaskDefinition._environmentFromJSON(
                        MockBoto3ECSClient._defaultTaskDefinitions[0][
                            "containerDefinitions"
                        ][0]["environment"]
                    )
                )
            ),
            min_size=1,
        ),
    )
    @settings(max_examples=50)
    def test_environment_unset(
        self, clusterName: str, serviceName: str, removes: List[str]
    ) -> None:
        with testingECSServiceClient() as clients:
            # Add starting data set
            self.initClusterAndService(clusterName, serviceName)

            # Run "environment" subcommand
            # Prefix variable names with "x" to make sure they start with a
            # letter
            result = clickTestRun(
                ECSServiceClient.main,
                [
                    "deploy_aws_ecs",
                    "environment",
                    "--cluster",
                    clusterName,
                    "--service",
                    serviceName,
                    *[f"x{k}" for k in removes],
                ],
            )

            self.assertEqual(len(clients), 1)
            client = clients[0]
            service = client.service

            self.assertEqual(service.cluster.name, clusterName)
            self.assertEqual(service.name, serviceName)

            resultEnvironment = client.currentTaskDefinition(
                service
            ).environment

        self.assertEqual(result.exitCode, 0)
        self.assertEqual(
            result.echoOutput,
            [
                (
                    f"Changing environment variables for "
                    f"{clusterName}:{serviceName}:",
                    {},
                )
            ]
            + [(f"    Removing x{k}.", {}) for k in removes],
        )
        self.assertEqual(result.stdout.getvalue(), "")
        self.assertEqual(result.stderr.getvalue(), "")

        for key in set(removes):
            self.assertNotIn(f"x{key}", resultEnvironment)
