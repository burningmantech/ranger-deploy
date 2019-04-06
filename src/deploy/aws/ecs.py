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
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence)

from attr import Factory, attrs

from boto3 import client as boto3Client

import click
from click import (
    Context as ClickContext, argument as commandArgument,
    group as commandGroup, option as commandOption,
    pass_context as passContext, version_option as versionOption,
)

from twisted.logger import Logger

from deploy.ext.click import readConfig
from deploy.ext.logger import startLogging


__all__ = (
    "TaskDefinition",
    "TaskEnvironment",
    "TaskEnvironmentUpdates",
    "NoChangesError",
    "ECSServiceClient",
)

Boto3ECSClient = Any

TaskDefinition = Mapping[str, Any]
TaskEnvironment = Mapping[str, str]
TaskEnvironmentUpdates = Mapping[str, Optional[str]]



class NoChangesError(Exception):
    """
    Changes requested without any updates.
    """



@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSServiceClient(object):
    """
    Elastic Container Service Client
    """

    #
    # Static methods
    #

    @staticmethod
    def _environmentAsJSON(
        environment: TaskEnvironment
    ) -> List[Dict[str, str]]:
        return [
            {"name": key, "value": value}
            for key, value in environment.items()
        ]


    @staticmethod
    def _environmentFromJSON(json: List[Dict[str, str]]) -> TaskEnvironment:
        return {e["name"]: e["value"] for e in json}


    @staticmethod
    def _taskImageName(taskDefinition: TaskDefinition) -> str:
        return taskDefinition["containerDefinitions"][0]["image"]


    @staticmethod
    def _taskEnvironment(taskDefinition: TaskDefinition) -> TaskEnvironment:
        return ECSServiceClient._environmentFromJSON(
            taskDefinition["containerDefinitions"][0]["environment"]
        )


    #
    # Class attributes
    #

    log = Logger()


    @classmethod
    def main(cls) -> None:
        """
        Command line entry point.
        """
        main()


    #
    # Instance attributes
    #

    cluster: str
    service: str

    _botoClient: List[Boto3ECSClient] = Factory(list)
    _currentTask: Dict[str, Any] = Factory(dict)


    @property
    def _aws(self) -> Boto3ECSClient:
        if not self._botoClient:
            self._botoClient.append(boto3Client("ecs"))
        return self._botoClient[0]


    def currentTaskARN(self) -> str:
        """
        Look up the ARN for the service's current task.
        """
        if "arn" not in self._currentTask:
            self.log.debug(
                "Looking up current task ARN for {cluster}:{service}...",
                cluster=self.cluster, service=self.service,
            )
            serviceDescription = self._aws.describe_services(
                cluster=self.cluster, services=[self.service]
            )
            self._currentTask["arn"] = (
                serviceDescription["services"][0]["taskDefinition"]
            )

        return self._currentTask["arn"]


    def currentTaskDefinition(self) -> TaskDefinition:
        """
        Look up the definition for the service's current task.
        """
        if "definition" not in self._currentTask:
            currentTaskARN = self.currentTaskARN()
            self.log.debug(
                "Looking up task definition for {arn}...", arn=currentTaskARN
            )
            currentTaskDescription = self._aws.describe_task_definition(
                taskDefinition=currentTaskARN
            )
            self._currentTask["definition"] = (
                currentTaskDescription["taskDefinition"]
            )

        return self._currentTask["definition"]


    def currentImageName(self) -> str:
        """
        Look up the Docker image name used for the service's current task.
        """
        currentTaskDefinition = self.currentTaskDefinition()
        return self._taskImageName(currentTaskDefinition)


    def updateTaskDefinition(
        self,
        imageName: Optional[str] = None,
        environment: Optional[TaskEnvironment] = None,
    ) -> TaskDefinition:
        """
        Update the definition for the service's current task.
        Returns the updated task definition.
        """
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

        # If no changes are being applied, there's nothing to do.
        newTaskDefinition["containerDefinitions"][0]["environment"] = (
            self._environmentAsJSON(environment)
        )
        if newTaskDefinition == currentTaskDefinition:
            raise NoChangesError()

        environment = dict(environment)

        # Record the current time
        environment["TASK_UPDATED"] = str(DateTime.utcnow())

        # If we're in Travis CI, record some information about the build.
        if environ.get("TRAVIS", "false") == "true":
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
        newTaskDefinition["containerDefinitions"][0]["environment"] = (
            self._environmentAsJSON(environment)
        )

        return newTaskDefinition


    def registerTaskDefinition(self, taskDefinition: TaskDefinition) -> str:
        """
        Register a new task definition for the service.
        """
        self.log.debug("Registering new task definition...")
        response = self._aws.register_task_definition(**taskDefinition)
        newTaskARN = response["taskDefinition"]["taskDefinitionArn"]
        self.log.info("Registered task definition: {arn}", arn=newTaskARN)

        return newTaskARN


    def currentTaskEnvironment(self) -> TaskEnvironment:
        """
        Look up the environment variables used for the service's current task.
        """
        currentTaskDefinition = self.currentTaskDefinition()

        # We don't handle tasks with multiple containers for now.
        assert len(currentTaskDefinition["containerDefinitions"]) == 1

        return self._taskEnvironment(currentTaskDefinition)


    def updateTaskEnvironment(
        self, updates: TaskEnvironmentUpdates
    ) -> TaskEnvironment:
        """
        Update the environment variables for the service's current task.
        Returns the updated task environment.
        """
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


    def deployTask(self, arn: str) -> None:
        """
        Deploy a new task to the service.
        """
        self.log.debug(
            "Deploying task ARN {arn} to service {cluster}:{service}...",
            cluster=self.cluster, service=self.service, arn=arn
        )
        self._currentTask.clear()
        self._aws.update_service(
            cluster=self.cluster, service=self.service, taskDefinition=arn
        )
        self.log.info(
            "Deployed task ARN {arn} to service {cluster}:{service}.",
            cluster=self.cluster, service=self.service, arn=arn
        )


    def deployTaskDefinition(self, taskDefinition: TaskDefinition) -> None:
        """
        Register a new task definition and deploy it to the service.
        """
        arn = self.registerTaskDefinition(taskDefinition)
        self.deployTask(arn)


    def deployImage(self, imageName: str, trialRun: bool = False) -> None:
        """
        Deploy a Docker Image to the service.
        """
        try:
            newTaskDefinition = self.updateTaskDefinition(imageName=imageName)
        except NoChangesError:
            self.log.info(
                "Image is the unchanged. Nothing to deploy."
            )
            return

        self.log.debug(
            "Deploying image {image} to service {cluster}:{service}...",
            cluster=self.cluster, service=self.service, image=imageName
        )
        if not trialRun:
            self.deployTaskDefinition(newTaskDefinition)
        self.log.info(
            "Deployed image {image} to service {cluster}:{service}.",
            cluster=self.cluster, service=self.service, image=imageName
        )


    def deployTaskEnvironment(self, updates: TaskEnvironmentUpdates) -> None:
        """
        Deploy a modifications to the environment variables used by the
        service.
        """
        if not updates:
            return

        newTaskEnvironment = self.updateTaskEnvironment(updates)

        try:
            newTaskDefinition = self.updateTaskDefinition(
                environment=newTaskEnvironment
            )
        except NoChangesError:
            self.log.info(
                "No changes made to task environment. Nothing to deploy."
            )
            return

        self.log.debug(
            "Deploying task environment to service {cluster}:{service}...",
            cluster=self.cluster, service=self.service, updates=updates
        )
        self.deployTaskDefinition(newTaskDefinition)
        self.log.info(
            "Deployed task environment to service {cluster}:{service}.",
            cluster=self.cluster, service=self.service, updates=updates
        )


    def rollback(self) -> None:
        """
        Deploy the most recently deployed task definition prior to the one
        currently used by service.
        """
        currentTaskDefinition = self.currentTaskDefinition()

        family = currentTaskDefinition["family"]
        response = self._aws.list_task_definitions(familyPrefix=family)

        # Deploy second-to-last ARN
        taskARN = response["taskDefinitionArns"][-2]

        self.deployTask(taskARN)



#
# Command line
#

def ecsOption(optionName: str, environment: Optional[str] = None) -> Callable:
    if environment is None:
        flag = f"--{optionName}"
        help = f"ECS {optionName}"
        environment = "staging"
    else:
        flag = f"--{environment}-{optionName}"
        help = f"ECS {optionName} for the {environment} environment"

    return commandOption(
        flag, envvar=f"AWS_ECS_{optionName.upper()}_{environment.upper()}",
        help=help, type=str, metavar="<name>", prompt=True, required=True,
    )

clusterOption           = ecsOption("cluster")
serviceOption           = ecsOption("service")
stagingClusterOption    = ecsOption("cluster", "staging")
stagingServiceOption    = ecsOption("service", "staging")
productionClusterOption = ecsOption("cluster", "production")
productionServiceOption = ecsOption("service", "production")


@commandGroup()
@versionOption()
@commandOption(
    "--profile",
    help="Profile to load from configuration file",
    type=str, metavar="<name>", prompt=False, required=False,
)
@passContext
def main(ctx: ClickContext, profile: Optional[str]) -> None:
    """
    AWS Elastic Container Service deployment tool.
    """
    if ctx.default_map is None:
        commonDefaults = readConfig(profile=profile)

        commonDefaults.setdefault(
            "cluster", commonDefaults.get("staging_cluster")
        )
        commonDefaults.setdefault(
            "service", commonDefaults.get("staging_service")
        )

        ctx.default_map = {
            command: commonDefaults for command in (
                "staging",
                "rollback",
                "production",
                "compare",
                "environment",
            )
        }

    startLogging()


@main.command()
@stagingClusterOption
@stagingServiceOption
@commandOption(
    "--image",
    envvar="AWS_ECR_IMAGE_NAME",
    help="Docker image to use",
    type=str, metavar="<name>", prompt=True, required=True,
)
@commandOption(
    "--trial-run", is_flag=True, help="Trial run only (do not deploy)"
)
def staging(
    staging_cluster: str, staging_service: str, image: str, trial_run: bool,
) -> None:
    """
    Deploy a new image to the staging environment.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    stagingClient.deployImage(image, trialRun=trial_run)


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
    productionClient.deployImage(stagingImageName)


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
        click.echo(f"{name} task ARN: {client.currentTaskARN()}")
        click.echo(f"{name} container image: {client.currentImageName()}")

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
        click.echo("Matching environment variables:")
        for key in sorted(same):
            click.echo(f"    {key} = {stagingEnvironment[key]!r}")
    if different:
        click.echo("Mismatching environment variables:")
        for key in sorted(different):
            click.echo(
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
        click.echo(f"Changing environment variables for {cluster}:{service}:")
        updates: Dict[str, Optional[str]] = {}
        for arg in arguments:
            if "=" in arg:
                key, value = arg.split("=", 1)
                updates[key] = value
                click.echo(f"    Setting {key}.")
            else:
                updates[arg] = None
                click.echo(f"    Removing {arg}.")

        stagingClient.deployTaskEnvironment(updates)
    else:
        currentTaskEnvironment = stagingClient.currentTaskEnvironment()
        click.echo(f"Environment variables for {cluster}:{service}:")
        for key, value in currentTaskEnvironment.items():
            click.echo(f"    {key} = {value!r}")



if __name__ == "__main__":  # pragma: no cover
    ECSServiceClient.main()
