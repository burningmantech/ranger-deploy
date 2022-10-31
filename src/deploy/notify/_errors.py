"""
Notification Errors.
"""

from attr import attrs


__all__ = ("FailedToSendNotificationError",)


@attrs(auto_attribs=True, auto_exc=True, slots=True)
class FailedToSendNotificationError(Exception):
    """
    Failed to send notification.
    """

    message: str
