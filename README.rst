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

Deploying to Amazon Web Services
--------------------------------

Services may be deployed to AWS via the ``deploy_aws`` command.  ``deploy_aws`` will presently only supported for deployments to the Elastic Container Service and has only been tested with services using Fargate.

``deploy_aws`` has a number of subcommands:

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

  deploy_aws <subcommand> --help

The following environment variables are used if the corresponding command line options are omitted:

``AWS_ECS_CLUSTER_STAGING``
  ``--staging-cluster`` or ``--cluster``

``AWS_ECS_SERVICE_STAGING``
  ``--staging-service`` or ``--service``

``AWS_ECS_CLUSTER_PRODUCTION``
  ``--production-cluster``

``AWS_ECS_SERVICE_PRODUCTION``
  ``--production-service``
