"""
Notification Errors.
"""

from attrs import mutable


__all__ = ("FailedToSendNotificationError",)


@mutable
class FailedToSendNotificationError(Exception):
    """
    Failed to send notification.
    """

    message: str
