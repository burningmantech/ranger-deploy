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
    Iterable,
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
    "ECSCluster",
    "ECSService",
    "ECSServiceClient",
    "ECSTaskDefinition",
    "NoChangesError",
    "NoSuchServiceError",
)


log = Logger()

Boto3ECSClient = Any

TaskDefinitionJSON = Mapping[str, Any]
TaskEnvironment = Mapping[str, str]
TaskEnvironmentUpdates = Mapping[str, Optional[str]]


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSTaskDefinition(object):
    """
    ECS Task Definition.
    """

    #
    # Static attributes
    #

    @staticmethod
    def _environmentFromJSON(json: List[Dict[str, str]]) -> TaskEnvironment:
        return {e["name"]: e["value"] for e in json}

    @staticmethod
    def _environmentAsJSON(
        environment: TaskEnvironment,
    ) -> List[Dict[str, str]]:
        return [
            {"name": key, "value": value} for key, value in environment.items()
        ]

    @staticmethod
    def _taskImageName(json: TaskDefinitionJSON) -> str:
        return cast(str, json["containerDefinitions"][0]["image"])

    #
    # Instance attributes
    #

    json: TaskDefinitionJSON

    @property
    def arn(self) -> Optional[str]:
        """
        Image name.
        """
        return cast(Optional[str], self.json.get("taskDefinitionArn"))

    @property
    def imageName(self) -> str:
        """
        Image name.
        """
        return self._taskImageName(self.json)

    @property
    def environment(self) -> TaskEnvironment:
        """
        Container environment.
        """
        # We don't handle tasks with multiple containers for now.
        assert len(self.json["containerDefinitions"]) == 1

        return self._environmentFromJSON(
            self.json["containerDefinitions"][0]["environment"]
        )

    def update(
        self,
        imageName: Optional[str] = None,
        environment: Optional[TaskEnvironment] = None,
    ) -> "ECSTaskDefinition":
        """
        Update the task definition and return the updated task definition.
        """
        currentJSON = self.json

        # We don't handle tasks with multiple containers for now.
        assert len(currentJSON["containerDefinitions"]) == 1

        # Copy, then remove keys that may not be re-submitted.
        currentJSON = dict(currentJSON)
        del currentJSON["revision"]
        del currentJSON["status"]
        del currentJSON["taskDefinitionArn"]
        if "FARGATE" in currentJSON["compatibilities"]:
            del currentJSON["compatibilities"]
            del currentJSON["requiresAttributes"]

        # Deep copy the current task definition for editing.
        newJSON = deepcopy(currentJSON)

        if imageName is not None:
            # Edit the container image to the new one.
            newJSON["containerDefinitions"][0]["image"] = imageName

        if environment is None:
            # Start with current environment
            environment = self._environmentFromJSON(
                currentJSON["containerDefinitions"][0]["environment"]
            )

        # If no changes are being applied, there's nothing to do.
        newJSON["containerDefinitions"][0][
            "environment"
        ] = self._environmentAsJSON(environment)
        if newJSON == currentJSON:
            raise NoChangesError()

        newEnvironment = dict(environment)

        # Record the current time
        newEnvironment["TASK_UPDATED"] = str(DateTime.utcnow())

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
                newEnvironment[f"CI_{key}"] = value

        # Edit the container environment to the new one.
        newJSON["containerDefinitions"][0][
            "environment"
        ] = self._environmentAsJSON(newEnvironment)

        return self.__class__(json=newJSON)


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSCluster(object):
    """
    ECS Cluster
    """

    name: str

    def __str__(self) -> str:
        return f"{self.name}"


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSService(object):
    """
    ECS Service
    """

    #
    # Class attributes
    #

    @classmethod
    def fromNames(cls, clusterName: str, serviceName: str) -> "ECSService":
        """
        Create and return an ECSService created from a cluster name and service
        name.
        """
        return cls(cluster=ECSCluster(name=clusterName), name=serviceName)

    @classmethod
    def fromDescriptor(cls, descriptor: str) -> "ECSService":
        """
        Create and return an ECSService created from a string descriptor
        consisting of a cluster name and service name separated by a colon
        (eg. "cluster:service").
        """
        try:
            clusterName, serviceName = descriptor.split(":")
        except ValueError:
            raise ValueError(f"Invalid service descriptor: {descriptor}")

        return cls.fromNames(clusterName, serviceName)

    #
    # Instance attributes
    #

    cluster: ECSCluster
    name: str

    def __str__(self) -> str:
        return self.descriptor()

    def descriptor(self) -> str:
        """
        Return a string descriptor for the service, consisting of a cluster
        name and service name separated by a colon (eg. "cluster:service").
        """
        return f"{self.cluster}:{self.name}"


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class NoSuchServiceError(Exception):
    """
    Service does not exist in the specified cluster.
    """

    service: ECSService


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class NoChangesError(Exception):
    """
    Changes requested without any updates.
    """


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class ServiceStateError(Exception):
    """
    ECS service is in an incorrect state.
    """

    message: str


@attrs(frozen=True, auto_attribs=True, slots=True, kw_only=True)
class ECSServiceClient(object):
    """
    Elastic Container Service Client
    """

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

    _botoClient: List[Boto3ECSClient] = Factory(list)
    _currentTasks: Dict[ECSService, ECSTaskDefinition] = Factory(dict)

    @property
    def _aws(self) -> Boto3ECSClient:
        if not self._botoClient:
            self._botoClient.append(boto3Client("ecs"))
        return self._botoClient[0]

    def _lookupTaskARN(self, service: ECSService) -> str:
        self.log.debug(
            "Looking up current task definition ARN for {service}...",
            service=service,
        )
        serviceDescription = self._aws.describe_services(
            cluster=service.cluster.name, services=[service.name]
        )
        services = serviceDescription["services"]
        if not services:
            raise NoSuchServiceError(service)

        assert len(services) == 1

        return cast(str, services[0]["taskDefinition"])

    def _lookupTaskDefinition(self, arn: str) -> ECSTaskDefinition:
        self.log.debug("Looking up task definition for {arn}...", arn=arn)
        response = self._aws.describe_task_definition(taskDefinition=arn)
        definition = cast(TaskDefinitionJSON, response["taskDefinition"])
        assert definition.get("taskDefinitionArn") == arn
        return ECSTaskDefinition(json=definition)

    def currentTaskDefinition(self, service: ECSService) -> ECSTaskDefinition:
        """
        Return the current task definition for the given service.
        """
        if service not in self._currentTasks:
            arn = self._lookupTaskARN(service)
            self._currentTasks[service] = self._lookupTaskDefinition(arn)

        return self._currentTasks[service]

    def registerTaskDefinition(self, taskDefinition: ECSTaskDefinition) -> str:
        """
        Register a new task definition.
        """
        self.log.debug("Registering new task definition...")
        response = self._aws.register_task_definition(**taskDefinition.json)
        newTaskARN = cast(str, response["taskDefinition"]["taskDefinitionArn"])
        self.log.info("Registered task definition: {arn}", arn=newTaskARN)

        return newTaskARN

    def deployTaskWithARN(self, service: ECSService, arn: str) -> None:
        """
        Deploy the task with the given ARN to the service.
        """
        self.log.debug(
            "Deploying task ARN {arn} to service {service}...",
            service=service,
            arn=arn,
        )
        del self._currentTasks[service]
        self._aws.update_service(
            cluster=service.cluster.name,
            service=service.name,
            taskDefinition=arn,
        )
        self.log.info(
            "Deployed task ARN {arn} to service {service}.",
            service=service,
            arn=arn,
        )

    def deployTaskDefinition(
        self, service: ECSService, taskDefinition: ECSTaskDefinition
    ) -> None:
        """
        Register a new task definition and deploy it to the service.
        """
        arn = self.registerTaskDefinition(taskDefinition)
        self.deployTaskWithARN(service, arn)

    def deployImage(
        self, service: ECSService, imageName: str, trialRun: bool = False
    ) -> None:
        """
        Deploy a Docker Image to the service.
        """
        currentTaskDefinition = self.currentTaskDefinition(service)

        try:
            newTaskDefinition = currentTaskDefinition.update(
                imageName=imageName
            )
        except NoChangesError:
            self.log.info("Image name is unchanged. Nothing to deploy.")
            return

        self.log.debug(
            "Deploying image {image} to service {service}...",
            service=service,
            image=imageName,
        )
        if not trialRun:
            self.deployTaskDefinition(service, newTaskDefinition)
        self.log.info(
            "Deployed image {image} to service {service}.",
            service=service,
            image=imageName,
        )

    def updateTaskEnvironment(
        self, service: ECSService, updates: TaskEnvironmentUpdates
    ) -> TaskEnvironment:
        """
        Update the environment variables for the service's current task
        definition.
        Returns the updated task environment.
        """
        environment = dict(self.currentTaskDefinition(service).environment)

        for key, value in updates.items():
            if value is None:
                try:
                    del environment[key]
                except KeyError:
                    pass
            else:
                environment[key] = value

        return environment

    def deployTaskEnvironment(
        self, service: ECSService, updates: TaskEnvironmentUpdates
    ) -> None:
        """
        Deploy a modifications to the environment variables used by the
        service.
        """
        if not updates:
            return

        newTaskEnvironment = self.updateTaskEnvironment(service, updates)

        currentTaskDefinition = self.currentTaskDefinition(service)

        try:
            newTaskDefinition = currentTaskDefinition.update(
                environment=newTaskEnvironment
            )
        except NoChangesError:
            self.log.info(
                "No changes made to task environment. Nothing to deploy."
            )
            return

        self.log.debug(
            "Deploying task environment to service {service}...",
            service=service,
            updates=updates,
        )
        self.deployTaskDefinition(service, newTaskDefinition)
        self.log.info(
            "Deployed task environment to service {service}.",
            service=service,
            updates=updates,
        )

    def rollback(self, service: ECSService) -> None:
        """
        Deploy the most recently deployed task definition prior to the one
        currently used by service.
        """
        currentTaskDefinition = self.currentTaskDefinition(service).json

        family = currentTaskDefinition["family"]
        response = self._aws.list_task_definitions(familyPrefix=family)

        # Select the second-to-last task ARN
        arn = response["taskDefinitionArns"][-2]

        self.deployTaskWithARN(service, arn)


#
# Command line
#


def ensureCI() -> None:
    """
    Make sure we are in a CI environment.
    """
    if environ.get("TRAVIS", "false").lower() == "true":
        if environ.get("TRAVIS_PULL_REQUEST") != "false":
            log.critical("Attempted deployment from pull request")
            raise UsageError("Deployment not allowed from pull request")

        branch = environ.get("TRAVIS_BRANCH")
        deploymentBranch = environ.get("DEPLOY_FROM_CI_BRANCH", "master")

        if branch != deploymentBranch:
            log.critical(
                "Attempted deployment from non-{deploymentBranch} "
                "branch {branch}",
                deploymentBranch=deploymentBranch,
                branch=branch,
            )
            raise UsageError(
                f"Deployment not allowed from branch {branch!r} "
                f"(must be {deploymentBranch!r})"
            )

    elif environ.get("CI", "false").lower() == "true":
        pass

    else:
        log.critical("Attempted deployment outside of CI")
        raise UsageError("Deployment not allowed outside of CI environment")


def servicesFromNames(
    cluster: Optional[str], service: str
) -> Iterable[ECSService]:
    if cluster is None:
        for descriptor in service.split(","):
            try:
                yield ECSService.fromDescriptor(descriptor)
            except ValueError as e:
                raise UsageError(str(e))
    else:
        log.warn("Cluster argument is deprecated")

        if ":" in service:
            raise UsageError(
                f"Invalid service (cluster re-specified): {service}"
            )

        yield ECSService(cluster=ECSCluster(name=cluster), name=service)


def serviceFromNames(cluster: Optional[str], service: str) -> ECSService:
    services = tuple(servicesFromNames(cluster, service))
    if len(services) > 1:
        raise UsageError("Multiple services specified")
    return services[0]


def ecsOption(
    optionName: str, environment: Optional[str] = None, required: bool = True
) -> Callable:
    if environment is None:
        flag = f"--{optionName}"
        help = f"ECS {optionName}"
        environment = "staging"
    else:
        flag = f"--{environment}-{optionName}"
        help = f"ECS {optionName} for the {environment} environment"

    return commandOption(
        flag,
        envvar=f"AWS_ECS_{optionName.upper()}_{environment.upper()}",
        help=help,
        type=str,
        metavar="<name>",
        prompt=required,
        required=required,
    )


environmentOptions = composedOptions(
    ecsOption("cluster", required=False), ecsOption("service"),
)
stagingEnvironmentOptions = composedOptions(
    ecsOption("cluster", "staging", required=False),
    ecsOption("service", "staging"),
)
productionEnvironmentOptions = composedOptions(
    ecsOption("cluster", "production", required=False),
    ecsOption("service", "production"),
)


@commandGroup()
@versionOption()
@profileOption
@passContext
def main(ctx: ClickContext, profile: Optional[str]) -> None:
    """
    AWS Elastic Container Service deployment tool.
    """
    if profile is None:
        click.echo("Using default profile")
    else:
        click.echo(f"Using profile: {profile}")

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
    staging_cluster: Optional[str],
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
        # We got an image name without a tag... use the Git commit hash.
        repo = Repo()
        commitID = repo.head.object.hexsha
        image_ecr = f"{image_ecr}:{commitID[:7]}"

    if image_local:
        ecrClient = ECRServiceClient()
        ecrClient.push(image_local, image_ecr, trialRun=trial_run)

    ecsClient = ECSServiceClient()

    for stagingService in servicesFromNames(staging_cluster, staging_service):
        try:
            ecsClient.deployImage(stagingService, image_ecr, trialRun=trial_run)
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
def rollback(staging_cluster: Optional[str], staging_service: str) -> None:
    """
    Roll back the staging environment to the previous task definition.
    """
    ecsClient = ECSServiceClient()

    for stagingService in servicesFromNames(staging_cluster, staging_service):
        ecsClient.rollback(stagingService)


@main.command()
@stagingEnvironmentOptions
@productionEnvironmentOptions
def production(
    staging_cluster: Optional[str],
    staging_service: str,
    production_cluster: Optional[str],
    production_service: str,
) -> None:
    """
    Deploy the image in the staging environment to the production environment.
    """
    ecsClient = ECSServiceClient()

    stagingImageName: Optional[str] = None
    for stagingService in servicesFromNames(staging_cluster, staging_service):
        imageName = ecsClient.currentTaskDefinition(stagingService).imageName
        if stagingImageName is None:
            stagingImageName = imageName
        else:
            if imageName != stagingImageName:
                raise ServiceStateError(
                    f"Staging service image names do not match: "
                    f"{imageName!r} != {stagingImageName!r}"
                )

    assert stagingImageName is not None

    for productionService in servicesFromNames(
        production_cluster, production_service
    ):
        ecsClient.deployImage(productionService, stagingImageName)


@main.command()
@stagingEnvironmentOptions
@productionEnvironmentOptions
def compare(
    staging_cluster: Optional[str],
    staging_service: str,
    production_cluster: Optional[str],
    production_service: str,
) -> None:
    """
    Compare the staging environment to the production environment.
    """
    ecsClient = ECSServiceClient()

    stagingService = serviceFromNames(staging_cluster, staging_service)
    productionService = serviceFromNames(production_cluster, production_service)

    stagingTask = ecsClient.currentTaskDefinition(stagingService)
    productionTask = ecsClient.currentTaskDefinition(productionService)

    for name, task in (
        ("Staging", stagingTask),
        ("Producton", productionTask),
    ):
        click.echo(f"{name} task ARN: {task.arn}")
        click.echo(f"{name} container image: {task.imageName}")

    stagingEnvironment = stagingTask.environment
    productionEnvironment = productionTask.environment

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
def environment(
    cluster: Optional[str], service: str, arguments: Sequence[str]
) -> None:
    """
    Show or modify environment variables for a service.

    If no arguments are given, prints all environment variable name/value
    pairs.

    If arguments are given, set environment variables with the given names to
    the given values.  If a value is not provided, remove the variable.
    """
    ecsClient = ECSServiceClient()
    ecsService = serviceFromNames(cluster, service)
    if arguments:
        click.echo(f"Changing environment variables for {service}:")
        updates: Dict[str, Optional[str]] = {}
        for arg in arguments:
            if "=" in arg:
                key, value = arg.split("=", 1)
                updates[key] = value
                click.echo(f"    Setting {key}.")
            else:
                updates[arg] = None
                click.echo(f"    Removing {arg}.")

        ecsClient.deployTaskEnvironment(ecsService, updates)
    else:
        environment = ecsClient.currentTaskDefinition(ecsService).environment
        click.echo(f"Environment variables for {service}:")
        for key, value in environment.items():
            click.echo(f"    {key} = {value!r}")


if __name__ == "__main__":  # pragma: no cover
    ECSServiceClient.main()
