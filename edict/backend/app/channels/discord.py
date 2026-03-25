import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar

from .base import NotificationChannel


class DiscordChannel(NotificationChannel):
    name: ClassVar[str] = 'discord'
    label: ClassVar[str] = 'Discord'
    icon: ClassVar[str] = '🎮'
    placeholder: ClassVar[str] = 'https://discord.com/api/webhooks/.../...'
    allowed_domains: ClassVar[tuple[str, ...]] = ('discord.com', 'discordapp.com')

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        domain = cls._extract_domain(webhook)
        return any(domain.endswith(d) for d in cls.allowed_domains) and '/api/webhooks/' in webhook

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        embed = {
            'title': title,
            'description': content,
            'color': 5814783
        }
        if url:
            embed['url'] = url
        payload = json.dumps({'embeds': [embed]}).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status in (200, 204)
        except (URLError, HTTPError, Exception):
            return False