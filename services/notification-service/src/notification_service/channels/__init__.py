"""Channel implementations + Protocol для notification-service."""

from notification_service.channels.base import Channel
from notification_service.channels.in_app import InAppChannel
from notification_service.channels.log import LogChannel

__all__ = ["Channel", "InAppChannel", "LogChannel"]
