from __future__ import annotations

import json
import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar

from .base import NotificationChannel


class WebhookChannel(NotificationChannel):
    name: ClassVar[str] = 'webhook'
    label: ClassVar[str] = '通用 Webhook'
    icon: ClassVar[str] = '🔗'
    placeholder: ClassVar[str] = 'https://your-server.com/webhook/...'
    allowed_domains: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        return cls._has_public_destination(webhook)

    @classmethod
    def _has_public_destination(cls, webhook: str) -> bool:
        try:
            parsed = urlparse(webhook)
            if parsed.scheme != 'https' or not parsed.hostname:
                return False
            port = parsed.port or 443
            infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        except (OSError, ValueError):
            return False

        addresses = {info[4][0] for info in infos if info and info[4]}
        if not addresses:
            return False

        try:
            return all(ipaddress.ip_address(address).is_global for address in addresses)
        except ValueError:
            return False

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        if not cls.validate_webhook(webhook):
            return False

        payload = json.dumps({
            'title': title,
            'content': content,
            'url': url,
            'source': 'edict'
        }).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return 200 <= resp.status < 300
        except (URLError, HTTPError, Exception):
            return False
