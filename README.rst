ranger-deploy
=============

.. image:: https://api.travis-ci.com/burningmantech/ranger-deploy.svg?branch=master
    :target: https://travis-ci.com/burningmantech/ranger-deploy
    :alt: Build Status
.. image:: https://codecov.io/github/burningmantech/ranger-deploy/coverage.svg?branch=master
    :target: https://codecov.io/github/burningmantech/ranger-deploy?branch=master
    :alt: Code Coverage
.. image:: https://requires.io/github/burningmantech/ranger-deploy/requirements.svg?branch=master
    :target: https://requires.io/github/burningmantech/ranger-deploy/requirements/?branch=master
    :alt: Requirements Status

Deployment tools for Ranger services.


Deploying to Amazon Web Services Elastic Container Registry
-----------------------------------------------------------

Images may be deployed to AWS ECR via the ``deploy_aws_ecr`` command.

``deploy_aws_ecr`` has the following subcommands:

``push``
  Updates the staging environment to use a new Docker image.

For usage information, run::

  deploy_aws_ecs <subcommand> --help

``deploy_aws_ecr`` can alternatively be invoked as ``python -m deploy.aws.ecr``.


Deploying to Amazon Web Services Elastic Container Service
----------------------------------------------------------

Services may be deployed to AWS ECS via the ``deploy_aws_ecs`` command.  ``deploy_aws_ecs`` has only been tested with services using Fargate.

``deploy_aws_ecs`` has the following subcommands:

``staging``
  Updates the staging environment to use a new Docker image.

``rollback``
  Updates the staging environment to use the prior Docker image by updating the service to use task definition used prior to the current one.
  Running this twice will not continue to roll back; it will switch between two definitions.

``compare``
  Compares the staging environment with the production environment.

``production``
  Updates the production environment to use the same Docker images as currently used in the staging environment.
  This does not modify other attributes, including environment variables.

``environment``
  Views or updates the environment variables in the specified cluster and service.

For usage information, run::

  deploy_aws_ecs <subcommand> --help

The following environment variables are used if the corresponding command line options are omitted:

``AWS_ECS_CLUSTER_STAGING``
  ``--staging-cluster`` or ``--cluster``

``AWS_ECS_SERVICE_STAGING``
  ``--staging-service`` or ``--service``

``AWS_ECS_CLUSTER_PRODUCTION``
  ``--production-cluster``

``AWS_ECS_SERVICE_PRODUCTION``
  ``--production-service``

``deploy_aws_ecs`` can alternatively be invoked as ``python -m deploy.aws.ecs``.


Sending Email Notifications
---------------------------

An email notification can be sent after a deployment by running ``deploy_notify_smtp``.

``deploy_notify_smtp`` has the following subcommands:

``staging``
  Sends a notification for a deployment to the staging environment.

For usage information, run::

  deploy_notify_smtp <subcommand> --help

The following environment variables are used if the corresponding command line options are omitted:

``PROJECT_NAME``
  ``--project-name``

``REPOSITORY_ID``
  ``--repository-id``

``BUILD_NUMBER``
  ``--build-number``

``BUILD_URL``
  ``--build-url``

``COMMIT_ID``
  ``--commit-id``

``COMMIT_MESSAGE``
  ``--commit-message``

``NOTIFY_SMTP_HOST``
  ``--smtp-host``

``NOTIFY_SMTP_PORT``
  ``--smtp-port``

``NOTIFY_SMTP_USER``
  ``--smtp-user``

``NOTIFY_SMTP_PASSWORD``
  ``--smtp-password``

``NOTIFY_EMAIL_SENDER``
  ``--sender``

``NOTIFY_EMAIL_RECIPIENT``
  ``--recipient``

``deploy_notify_smtp`` can alternatively be invoked as ``python -m deploy.notify.smtp``.


Configuration
-------------

A configuration file ``~/ranger-deploy.ini`` may be used to specify defaults for any of the above arguments.
This file uses a simple INI format in which configuration keys correspond to command line options, with each sections named after a configuration profile.
For configuration keys, remove leading hyphens and replace hyphens with underbars.
For example::

  [rangers]

  github_org = burningmantech

  staging_cluster    = rangers
  production_cluster = rangers

  smtp_host     = smtp.example.com
  smtp_port     = 465
  smtp_user     = some_user
  smtp_password = C70D9FB9-53BC-489A-A08D-567D281583D9
  sender        = sender@example.com
  recipient     = recipient@example.com


  [clubhouse-api]

  repository_id = ${rangers:github_org}/ranger-clubhouse-api

  staging_cluster    = ${rangers:staging_cluster}
  production_cluster = ${rangers:production_cluster}
  staging_service    = ranger-clubhouse-api-staging-fg
  production_service = ranger-clubhouse-api-production-fg

  smtp_host     = ${rangers:smtp_host}
  smtp_port     = ${rangers:smtp_port}
  smtp_user     = ${rangers:smtp_user}
  smtp_password = ${rangers:smtp_password}
  sender        = ${rangers:sender}
  recipient     = ${rangers:recipient}


  [clubhouse-web]

  repository_id = ${rangers:github_org}/ranger-clubhouse-web

  staging_cluster    = ${rangers:staging_cluster}
  production_cluster = ${rangers:production_cluster}
  staging_service    = ranger-clubhouse-web-staging-fg
  production_service = ranger-clubhouse-web-production-fg

  smtp_host     = ${rangers:smtp_host}
  smtp_port     = ${rangers:smtp_port}
  smtp_user     = ${rangers:smtp_user}
  smtp_password = ${rangers:smtp_password}
  sender        = ${rangers:sender}
  recipient     = ${rangers:recipient}


  [clubhouse-classic]

  repository_id = ${rangers:github_org}/ranger-secret-clubhouse

  staging_cluster    = ${rangers:staging_cluster}
  production_cluster = ${rangers:production_cluster}
  staging_service    = ranger-secret-clubhouse-staging-fg
  production_service = ranger-secret-clubhouse-production-fg

  smtp_host     = ${rangers:smtp_host}
  smtp_port     = ${rangers:smtp_port}
  smtp_user     = ${rangers:smtp_user}
  smtp_password = ${rangers:smtp_password}
  sender        = ${rangers:sender}
  recipient     = ${rangers:recipient}


  [ims]

  repository_id = ${rangers:github_org}/ranger-ims-server

  staging_cluster    = ${rangers:staging_cluster}
  production_cluster = ${rangers:production_cluster}
  staging_service    = ranger-ims-staging-fg
  production_service = ranger-ims-production-fg

  smtp_host     = ${rangers:smtp_host}
  smtp_port     = ${rangers:smtp_port}
  smtp_user     = ${rangers:smtp_user}
  smtp_password = ${rangers:smtp_password}
  sender        = ${rangers:sender}
  recipient     = ${rangers:recipient}
