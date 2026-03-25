import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar

from .base import NotificationChannel


class SlackChannel(NotificationChannel):
    name: ClassVar[str] = 'slack'
    label: ClassVar[str] = 'Slack'
    icon: ClassVar[str] = '💬'
    placeholder: ClassVar[str] = 'https://hooks.slack.com/services/.../.../...'
    allowed_domains: ClassVar[tuple[str, ...]] = ('hooks.slack.com',)

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        domain = cls._extract_domain(webhook)
        return domain in cls.allowed_domains

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        blocks = [
            {
                'type': 'header',
                'text': {'type': 'plain_text', 'text': title}
            },
            {
                'type': 'section',
                'text': {'type': 'mrkdwn', 'text': content}
            }
        ]
        if url:
            blocks.append({
                'type': 'actions',
                'elements': [{
                    'type': 'button',
                    'text': {'type': 'plain_text', 'text': '查看详情'},
                    'url': url
                }]
            })
        payload = json.dumps({'blocks': blocks}).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status == 200
        except (URLError, HTTPError, Exception):
            return False