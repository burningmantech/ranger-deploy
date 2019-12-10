.. _deploy_api:

deploy API
==========


deploy
------

.. automodule:: deploy


deploy.aws.ecr
--------------

.. automodule:: deploy.aws.ecr

.. autoclass:: deploy.aws.ecr.ECRServiceClient


deploy.aws.ecs
--------------

.. automodule:: deploy.aws.ecs

.. autoclass::  deploy.aws.ecs.ECSServiceClient
.. automethod:: deploy.aws.ecs.ECSServiceClient.main
.. automethod:: deploy.aws.ecs.ECSServiceClient.currentTaskARN
.. automethod:: deploy.aws.ecs.ECSServiceClient.currentTaskDefinition
.. automethod:: deploy.aws.ecs.ECSServiceClient.currentImageName
.. automethod:: deploy.aws.ecs.ECSServiceClient.updateTaskDefinition
.. automethod:: deploy.aws.ecs.ECSServiceClient.registerTaskDefinition
.. automethod:: deploy.aws.ecs.ECSServiceClient.currentTaskEnvironment
.. automethod:: deploy.aws.ecs.ECSServiceClient.updateTaskEnvironment
.. automethod:: deploy.aws.ecs.ECSServiceClient.deployTask
.. automethod:: deploy.aws.ecs.ECSServiceClient.deployTaskDefinition
.. automethod:: deploy.aws.ecs.ECSServiceClient.deployImage
.. automethod:: deploy.aws.ecs.ECSServiceClient.deployTaskEnvironment
.. automethod:: deploy.aws.ecs.ECSServiceClient.rollback

.. autoclass::  deploy.aws.ecs.NoChangesError
.. autoclass::  deploy.aws.ecs.NoSuchServiceError


deploy.notify
-------------

.. automodule:: deploy.notify


deploy.notify.smtp
------------------

.. automodule:: deploy.notify.smtp

.. autoclass::  deploy.notify.smtp.SMTPNotifier
.. automethod:: deploy.notify.smtp.SMTPNotifier.main
.. automethod:: deploy.notify.smtp.SMTPNotifier.notifyStaging
