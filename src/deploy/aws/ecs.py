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
AWS Elastic Container Service support.
"""

from copy import deepcopy
from datetime import datetime as DateTime
from os import environ
from typing import Any, Dict, Mapping, Optional, Sequence

from boto3 import client as Boto3Client

from click import (
    argument as commandArgument, echo, group as commandGroup,
    option as commandOption, version_option as versionOption
)

TaskDefinition = Mapping[str, Any]
TaskEnvironment = Mapping[str, str]
TaskEnvironmentUpdates = Mapping[str, Optional[str]]


__all__ = (
    "NoChangesError",
    "ECSServiceClient",
)



class NoChangesError(Exception):
    pass



class ECSServiceClient(object):
    """
    ECS Service Client
    """

    @classmethod
    def main(cls) -> None:
        main()


    def __init__(self, cluster: str, service: str) -> None:
        self.cluster = cluster
        self.service = service

        self._currentTask: Dict[str, Any] = {}


    @property
    def _client(self) -> Boto3Client:
        if not hasattr(self, "_botoClient"):
            self._botoClient = Boto3Client("ecs")

        return self._botoClient


    def currentTaskARN(self) -> str:
        if "arn" not in self._currentTask:
            serviceDescription = self._client.describe_services(
                cluster=self.cluster, services=[self.service]
            )
            self._currentTask["arn"] = (
                serviceDescription["services"][0]["taskDefinition"]
            )

        return self._currentTask["arn"]


    def currentTaskDefinition(self) -> TaskDefinition:
        if "definition" not in self._currentTask:
            currentTaskARN = self.currentTaskARN()
            currentTaskDescription = self._client.describe_task_definition(
                taskDefinition=currentTaskARN
            )
            self._currentTask["definition"] = (
                currentTaskDescription["taskDefinition"]
            )

        return self._currentTask["definition"]


    def currentImageName(self) -> str:
        currentTaskDefinition = self.currentTaskDefinition()
        return currentTaskDefinition["containerDefinitions"][0]["image"]


    def updateTaskDefinition(
        self,
        imageName: Optional[str] = None,
        environment: Optional[TaskEnvironment] = None,
    ) -> TaskDefinition:
        currentTaskDefinition = self.currentTaskDefinition()

        # We don't handle tasks with multiple containers for now.
        assert len(currentTaskDefinition["containerDefinitions"]) == 1

        # Copy, then remove keys that may not be re-submitted.
        currentTaskDefinition = dict(currentTaskDefinition)
        del currentTaskDefinition["revision"]
        del currentTaskDefinition["status"]
        del currentTaskDefinition["taskDefinitionArn"]
        if "FARGATE" in currentTaskDefinition["compatibilities"]:
            del currentTaskDefinition["compatibilities"]
            del currentTaskDefinition["requiresAttributes"]

        # Deep copy the current task definition for editing.
        newTaskDefinition = deepcopy(currentTaskDefinition)

        if imageName is not None:
            # Edit the container image to the new one.
            newTaskDefinition["containerDefinitions"][0]["image"] = imageName

        if environment is None:
            # Start with current environment
            environment = self.currentTaskEnvironment()

        environment = dict(environment)

        environment["TASK_UPDATED"] = str(DateTime.utcnow())

        if environ.get("TRAVIS", "false") == "true":
            # If we're in Travis, capture some information about the build.
            for key in (
                "TRAVIS_COMMIT",
                "TRAVIS_COMMIT_MESSAGE",
                "TRAVIS_JOB_WEB_URL",
                "TRAVIS_PULL_REQUEST_BRANCH",
                "TRAVIS_TAG",
            ):
                value = environ.get(key, None)
                if value is not None:
                    environment[key] = value

        # Edit the container environment to the new one.
        newTaskDefinition["containerDefinitions"][0]["environment"] = [
            {"name": key, "value": value}
            for key, value in environment.items()
        ]

        # If no changes are being applied, there's nothing to do.
        if newTaskDefinition == currentTaskDefinition:
            print("No changes made to task definition. Nothing to deploy.")
            raise NoChangesError()

        return newTaskDefinition


    def registerTaskDefinition(self, taskDefinition: TaskDefinition) -> str:
        print("Registering new task definition...")
        response = self._client.register_task_definition(**taskDefinition)
        newTaskARN = (response["taskDefinition"]["taskDefinitionArn"])
        print("Registered", newTaskARN)

        return newTaskARN


    def currentTaskEnvironment(self) -> TaskEnvironment:
        currentTaskDefinition = self.currentTaskDefinition()

        # We don't handle tasks with multiple containers for now.
        assert len(currentTaskDefinition["containerDefinitions"]) == 1

        return {
            e["name"]: e["value"] for e in
            currentTaskDefinition["containerDefinitions"][0]["environment"]
        }


    def updateTaskEnvironment(
        self, updates: TaskEnvironmentUpdates
    ) -> TaskEnvironment:
        environment = dict(self.currentTaskEnvironment())

        for key, value in updates.items():
            if value is None:
                try:
                    del environment[key]
                except KeyError:
                    pass
            else:
                environment[key] = value

        return environment


    def updateService(self, arn: str) -> None:
        print(
            f"Updating service {self.cluster}:{self.service} to ARN {arn}..."
        )
        self._currentTask = {}
        self._client.update_service(
            cluster=self.cluster, service=self.service, taskDefinition=arn
        )


    def deployNewTaskDefinition(self, taskDefinition: TaskDefinition) -> None:
        arn = self.registerTaskDefinition(taskDefinition)
        self.updateService(arn)


    def deployNewImage(self, imageName: str) -> None:
        try:
            newTaskDefinition = self.updateTaskDefinition(imageName=imageName)
        except NoChangesError:
            return

        self.deployNewTaskDefinition(newTaskDefinition)

        print(
            f"Deployed new image {imageName} "
            f"to service {self.cluster}:{self.service}."
        )


    def deployTaskEnvironment(self, updates: TaskEnvironmentUpdates) -> None:
        if not updates:
            return

        newTaskEnvironment = self.updateTaskEnvironment(updates)

        try:
            newTaskDefinition = self.updateTaskDefinition(
                environment=newTaskEnvironment
            )
        except NoChangesError:
            return

        print(newTaskDefinition)

        self.deployNewTaskDefinition(newTaskDefinition)

        print(
            f"Deployed new task environment "
            f"to service {self.cluster}:{self.service}."
        )


    def rollback(self) -> None:
        currentTaskDefinition = self.currentTaskDefinition()

        family = currentTaskDefinition["family"]
        response = self._client.list_task_definitions(familyPrefix=family)

        # Deploy second-to-last ARN
        taskARN = response["taskDefinitionArns"][-2]

        self.updateService(taskARN)

        print("Rolled back to prior task ARN:", taskARN)



#
# Command line
#

clusterOption = commandOption(
    "--cluster",
    type=str, metavar="<name>",
    help="ECS cluster",
    envvar="AWS_ECS_CLUSTER_STAGING",
    prompt=True,
)
serviceOption = commandOption(
    "--service",
    type=str, metavar="<name>",
    help="ECS service",
    envvar="AWS_ECS_SERVICE_STAGING",
    prompt=True,
)
stagingClusterOption = commandOption(
    "--staging-cluster",
    type=str, metavar="<name>",
    help="ECS cluster for the staging environment",
    envvar="AWS_ECS_CLUSTER_STAGING",
    prompt=True,
)
stagingServiceOption = commandOption(
    "--staging-service",
    type=str, metavar="<name>",
    help="ECS service for the staging environment",
    envvar="AWS_ECS_SERVICE_STAGING",
    prompt=True,
)
productionClusterOption = commandOption(
    "--production-cluster",
    type=str, metavar="<name>",
    help="ECS cluster for the production environment",
    envvar="AWS_ECS_CLUSTER_PRODUCTION",
    prompt=True,
)
productionServiceOption = commandOption(
    "--production-service",
    type=str, metavar="<name>",
    help="ECS service for the production environment",
    envvar="AWS_ECS_SERVICE_PRODUCTION",
    prompt=True,
)


@commandGroup()
@versionOption()
def main() -> None:
    """
    AWS Elastic Container Service deployment tool.
    """


@main.command()
@stagingClusterOption
@stagingServiceOption
@commandOption(
    "--image",
    type=str, metavar="<name>",
    help="Docker image to use",
    envvar="AWS_ECR_IMAGE_NAME",
    prompt=True,
)
def staging(
    staging_cluster: str, staging_service: str, image: str
) -> None:
    """
    Deploy a new image to the staging environment.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    stagingClient.deployNewImage(image)


@main.command()
@stagingClusterOption
@stagingServiceOption
def rollback(
    staging_cluster: str, staging_service: str,
) -> None:
    """
    Roll back the staging environment to the previous task definition.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    stagingClient.rollback()


@main.command()
@stagingClusterOption
@stagingServiceOption
@productionClusterOption
@productionServiceOption
def production(
    staging_cluster: str, staging_service: str,
    production_cluster: str, production_service: str,
) -> None:
    """
    Deploy the image in the staging environment to the production environment.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    productionClient = ECSServiceClient(
        cluster=production_cluster, service=production_service
    )
    stagingImageName = stagingClient.currentImageName()
    productionClient.deployNewImage(stagingImageName)


@main.command()
@stagingClusterOption
@stagingServiceOption
@productionClusterOption
@productionServiceOption
def compare(
    staging_cluster: str, staging_service: str,
    production_cluster: str, production_service: str,
) -> None:
    """
    Compare the staging environment to the production environment.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    productionClient = ECSServiceClient(
        cluster=production_cluster, service=production_service
    )

    for name, client in (
        ("Staging", stagingClient),
        ("Producton", productionClient),
    ):
        echo(f"{name} task ARN: {client.currentTaskARN()}")
        echo(f"{name} container image: {client.currentImageName()}")

    stagingEnvironment = stagingClient.currentTaskEnvironment()
    productionEnvironment = productionClient.currentTaskEnvironment()

    keys = frozenset(
        tuple(stagingEnvironment.keys()) +
        tuple(productionEnvironment.keys())
    )

    same = set()
    different = set()

    for key in keys:
        stagingValue = stagingEnvironment.get(key, None)
        productionValue = productionEnvironment.get(key, None)

        if stagingValue == productionValue:
            same.add(key)
        else:
            different.add(key)

    if same:
        echo("Matching environment variables:")
        for key in same:
            echo(f"    {key} = {stagingEnvironment[key]!r}")
    if different:
        echo("Mismatching environment variables:")
        for key in different:
            echo(
                f"    {key} = "
                f"{stagingEnvironment.get(key)!r} / "
                f"{productionEnvironment.get(key)!r}"
            )


@main.command()
@clusterOption
@serviceOption
@commandArgument("arguments", nargs=-1, metavar="[name[=value]]")
def environment(cluster: str, service: str, arguments: Sequence[str]) -> None:
    """
    Show or modify environment variables.

    If no arguments are given, prints all environment variable name/value
    pairs.

    If arguments are given, set environment variables with the given names to
    the given values.  If a value is not provided, remove the variable.
    """
    stagingClient = ECSServiceClient(cluster=cluster, service=service)
    if arguments:
        echo(f"Changing environment variables for {cluster}:{service}:")
        updates: Dict[str, Optional[str]] = {}
        for arg in arguments:
            if "=" in arg:
                key, value = arg.split("=", 1)
                updates[key] = value
                echo(f"    Setting {key}.")
            else:
                updates[arg] = None
                echo(f"    Removing {arg}.")

        stagingClient.deployTaskEnvironment(updates)
    else:
        echo(f"Environment variables for {cluster}:{service}:")
        for key, value in (
            stagingClient.currentTaskEnvironment().items()
        ):
            echo(f"    {key} = {value!r}")
