import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar

from .base import NotificationChannel


class WebhookChannel(NotificationChannel):
    name: ClassVar[str] = 'webhook'
    label: ClassVar[str] = '通用 Webhook'
    icon: ClassVar[str] = '🔗'
    placeholder: ClassVar[str] = 'https://your-webhook-url/...'
    allowed_domains: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        return cls._validate_url_scheme(webhook)

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        payload = json.dumps({
            'title': title,
            'content': content,
            'url': url
        }).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status == 200
        except (URLError, HTTPError, Exception):
            return False