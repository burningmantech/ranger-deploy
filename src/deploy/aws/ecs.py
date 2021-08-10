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
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    cast,
)

from attr import Factory, attrs

from boto3 import client as boto3Client

import click
from click import (
    Context as ClickContext,
    UsageError,
    argument as commandArgument,
    group as commandGroup,
    option as commandOption,
    pass_context as passContext,
    version_option as versionOption,
)

from git import Repo

from twisted.logger import Logger

from deploy.ext.click import (
    composedOptions,
    profileOption,
    readConfig,
    trialRunOption,
)
from deploy.ext.logger import startLogging
from deploy.notify.smtp import (
    _staging as notifyStaging,
    buildOptions,
    smtpOptions,
)

from .ecr import ECRServiceClient


__all__ = (
    "TaskDefinition",
    "TaskEnvironment",
    "TaskEnvironmentUpdates",
    "NoChangesError",
    "NoSuchServiceError",
    "ECSServiceClient",
)


log = Logger()

Boto3ECSClient = Any

TaskDefinition = Mapping[str, Any]
TaskEnvironment = Mapping[str, str]
TaskEnvironmentUpdates = Mapping[str, Optional[str]]


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class NoSuchServiceError(Exception):
    """
    Service does not exist in the specified cluster.
    """

    service: str


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class NoChangesError(Exception):
    """
    Changes requested without any updates.
    """


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSServiceClient:
    """
    Elastic Container Service Client
    """

    #
    # Static methods
    #

    @staticmethod
    def _environmentAsJSON(
        environment: TaskEnvironment,
    ) -> List[Dict[str, str]]:
        return [
            {"name": key, "value": value} for key, value in environment.items()
        ]

    @staticmethod
    def _environmentFromJSON(json: List[Dict[str, str]]) -> TaskEnvironment:
        return {e["name"]: e["value"] for e in json}

    @staticmethod
    def _taskImageName(taskDefinition: TaskDefinition) -> str:
        return cast(str, taskDefinition["containerDefinitions"][0]["image"])

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
                cluster=self.cluster,
                service=self.service,
            )
            try:
                serviceDescription = self._aws.describe_services(
                    cluster=self.cluster, services=[self.service]
                )
            except Exception:
                self.log.critical(
                    "Unable to look up service description for "
                    "{cluster}:{service}",
                    cluster=self.cluster,
                    service=self.service,
                )
                raise
            services = serviceDescription["services"]
            if not services:
                raise NoSuchServiceError(self.service)
            self._currentTask["arn"] = services[0]["taskDefinition"]

        return cast(str, self._currentTask["arn"])

    def currentTaskDefinition(self) -> TaskDefinition:
        """
        Look up the definition for the service's current task.
        """
        if "definition" not in self._currentTask:
            currentTaskARN = self.currentTaskARN()
            self.log.debug(
                "Looking up task definition for {arn}...", arn=currentTaskARN
            )
            try:
                currentTaskDescription = self._aws.describe_task_definition(
                    taskDefinition=currentTaskARN
                )
            except Exception:
                self.log.critical(
                    "Unable to look up task definition for {arn}",
                    arn=currentTaskARN,
                )
                raise
            self._currentTask["definition"] = currentTaskDescription[
                "taskDefinition"
            ]

        return cast(TaskDefinition, self._currentTask["definition"])

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
        extraFields: Tuple[str, ...] = (
            "revision",
            "registeredAt",
            "registeredBy",
            "status",
            "taskDefinitionArn",
        )
        if "FARGATE" in currentTaskDefinition["compatibilities"]:
            extraFields += (
                "compatibilities",
                "requiresAttributes",
            )

        for field in extraFields:
            if field in currentTaskDefinition:
                del currentTaskDefinition[field]

        # Deep copy the current task definition for editing.
        newTaskDefinition = deepcopy(currentTaskDefinition)

        if imageName is not None:
            # Edit the container image to the new one.
            newTaskDefinition["containerDefinitions"][0]["image"] = imageName

        if environment is None:
            # Start with current environment
            environment = self.currentTaskEnvironment()

        # If no changes are being applied, there's nothing to do.
        newTaskDefinition["containerDefinitions"][0][
            "environment"
        ] = self._environmentAsJSON(environment)
        if newTaskDefinition == currentTaskDefinition:
            raise NoChangesError()

        environment = dict(environment)

        # Record the current time
        environment["TASK_UPDATED"] = str(DateTime.utcnow())

        # Record some information about the CI build.
        # FIXME: Get these values from parsed CLI, not environment.
        for key in (
            "BUILD_NUMBER",
            "BUILD_URL",
            "COMMIT_ID",
            "COMMIT_MESSAGE",
            "PROJECT_NAME",
            "REPOSITORY_ID",
        ):
            value = environ.get(key, None)
            if value is not None:
                environment[f"CI_{key}"] = value

        # Edit the container environment to the new one.
        newTaskDefinition["containerDefinitions"][0][
            "environment"
        ] = self._environmentAsJSON(environment)

        return newTaskDefinition

    def registerTaskDefinition(self, taskDefinition: TaskDefinition) -> str:
        """
        Register a new task definition for the service.
        """
        self.log.debug("Registering new task definition...")
        try:
            response = self._aws.register_task_definition(**taskDefinition)
        except Exception:
            self.log.critical(
                "Unable to register task definition: {taskDefinition}",
                taskDefinition=taskDefinition,
            )
            raise
        newTaskARN = cast(str, response["taskDefinition"]["taskDefinitionArn"])
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
            cluster=self.cluster,
            service=self.service,
            arn=arn,
        )
        self._currentTask.clear()
        try:
            self._aws.update_service(
                cluster=self.cluster, service=self.service, taskDefinition=arn
            )
        except Exception:
            self.log.critical(
                "Unable to deploy task ARN {arn} to service "
                "{cluster}:{service}",
                cluster=self.cluster,
                service=self.service,
                arn=arn,
            )
            raise
        self.log.info(
            "Deployed task ARN {arn} to service {cluster}:{service}.",
            cluster=self.cluster,
            service=self.service,
            arn=arn,
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
            self.log.info("Image name is unchanged. Nothing to deploy.")
            return

        self.log.debug(
            "Deploying image {image} to service {cluster}:{service}...",
            cluster=self.cluster,
            service=self.service,
            image=imageName,
        )
        if not trialRun:
            self.deployTaskDefinition(newTaskDefinition)
        self.log.info(
            "Deployed image {image} to service {cluster}:{service}.",
            cluster=self.cluster,
            service=self.service,
            image=imageName,
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
            cluster=self.cluster,
            service=self.service,
            updates=updates,
        )
        self.deployTaskDefinition(newTaskDefinition)
        self.log.info(
            "Deployed task environment to service {cluster}:{service}.",
            cluster=self.cluster,
            service=self.service,
            updates=updates,
        )

    def rollback(self) -> None:
        """
        Deploy the most recently deployed task definition prior to the one
        currently used by service.
        """
        currentTaskDefinition = self.currentTaskDefinition()

        family = currentTaskDefinition["family"]
        try:
            response = self._aws.list_task_definitions(familyPrefix=family)
        except Exception:
            self.log.critical(
                "Unable to list task definitions for task family with "
                "prefix {prefix}",
                prefix=family,
            )
            raise

        # Deploy second-to-last ARN
        taskARN = response["taskDefinitionArns"][-2]

        self.deployTask(taskARN)


#
# Command line
#


def ensureCI() -> None:
    """
    Make sure we are in a CI environment.
    """
    if environ.get("CI", "false").lower() == "true":
        pass

    else:
        log.critical("Attempted deployment outside of CI")
        raise UsageError("Deployment not allowed outside of CI environment")


def ecsOption(optionName: str, environment: Optional[str] = None) -> Callable:
    if environment is None:
        flag = f"--{optionName}"
        help = f"ECS {optionName}"
        environment = "staging"
    else:
        flag = f"--{environment}-{optionName}"
        help = f"ECS {optionName} for the {environment} environment"

    return cast(
        Callable,
        commandOption(
            flag,
            envvar=f"AWS_ECS_{optionName.upper()}_{environment.upper()}",
            help=help,
            type=str,
            metavar="<name>",
            prompt=True,
            required=True,
        ),
    )


environmentOptions = composedOptions(ecsOption("cluster"), ecsOption("service"))
stagingEnvironmentOptions = composedOptions(
    ecsOption("cluster", "staging"), ecsOption("service", "staging")
)
productionEnvironmentOptions = composedOptions(
    ecsOption("cluster", "production"), ecsOption("service", "production")
)


@commandGroup()
@versionOption()
@profileOption
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
            command: commonDefaults
            for command in (
                "staging",
                "rollback",
                "production",
                "compare",
                "environment",
            )
        }

    startLogging()


@main.command()
@stagingEnvironmentOptions
@buildOptions(required=False)
@smtpOptions(required=False)
@commandOption(
    "--image-local",
    envvar="LOCAL_IMAGE_NAME",
    help="Local Docker image to push to ECR",
    type=str,
    metavar="<name>",
    prompt=False,
    required=False,
)
@commandOption(
    "--image-ecr",
    envvar="AWS_ECR_IMAGE_NAME",
    help=(
        "ECR Docker image to push into"
        " (if no tag included, use shortened commit ID as tag)"
    ),
    type=str,
    metavar="<name>",
    prompt=True,
    required=False,
)
@trialRunOption
def staging(
    staging_cluster: str,
    staging_service: str,
    project_name: Optional[str],
    repository_id: Optional[Tuple[str, str, str]],
    build_number: str,
    build_url: str,
    commit_id: str,
    commit_message: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_sender: str,
    email_recipient: str,
    image_local: str,
    image_ecr: str,
    trial_run: bool,
) -> None:
    """
    Deploy a new image to the staging environment.
    """
    if not trial_run:
        ensureCI()

    if ":" not in image_ecr:
        repo = Repo()
        commitID = repo.head.object.hexsha
        image_ecr = f"{image_ecr}:{commitID[:7]}"

    if image_local:
        ecrClient = ECRServiceClient()
        ecrClient.push(image_local, image_ecr, trialRun=trial_run)

    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    try:
        stagingClient.deployImage(image_ecr, trialRun=trial_run)
    except NoSuchServiceError as e:
        raise UsageError(f"Unknown service: {e.service}")

    if (
        repository_id is not None
        and smtp_host
        and smtp_port
        and smtp_user
        and smtp_password
        and email_sender
        and email_recipient
    ):
        notifyStaging(
            project_name=project_name,
            repository_id=repository_id,
            build_number=build_number,
            build_url=build_url,
            commit_id=commit_id,
            commit_message=commit_message,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            email_sender=email_sender,
            email_recipient=email_recipient,
            trial_run=trial_run,
        )
    else:
        log.info("SMTP notification not configured")


@main.command()
@stagingEnvironmentOptions
def rollback(
    staging_cluster: str,
    staging_service: str,
) -> None:
    """
    Roll back the staging environment to the previous task definition.
    """
    stagingClient = ECSServiceClient(
        cluster=staging_cluster, service=staging_service
    )
    stagingClient.rollback()


@main.command()
@stagingEnvironmentOptions
@productionEnvironmentOptions
def production(
    staging_cluster: str,
    staging_service: str,
    production_cluster: str,
    production_service: str,
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
@stagingEnvironmentOptions
@productionEnvironmentOptions
def compare(
    staging_cluster: str,
    staging_service: str,
    production_cluster: str,
    production_service: str,
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
        tuple(stagingEnvironment.keys()) + tuple(productionEnvironment.keys())
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
@environmentOptions
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
