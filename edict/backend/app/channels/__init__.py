from typing import Type

from .base import NotificationChannel
from .feishu import FeishuChannel


CHANNELS: dict[str, Type[NotificationChannel]] = {
    'feishu': FeishuChannel,
}


def get_channel(channel_type: str) -> Type[NotificationChannel] | None:
    return CHANNELS.get(channel_type)


def get_all_channels() -> list[Type[NotificationChannel]]:
    return list(CHANNELS.values())


def get_channel_info() -> list[dict]:
    return [
        {
            'id': ch.name,
            'label': ch.label,
            'icon': ch.icon,
            'placeholder': ch.placeholder,
        }
        for ch in CHANNELS.values()
    ]


__all__ = [
    'NotificationChannel',
    'FeishuChannel',
    'CHANNELS',
    'get_channel',
    'get_all_channels',
    'get_channel_info',
]