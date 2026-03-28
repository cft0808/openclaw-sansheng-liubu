import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar, Optional

from .base import NotificationChannel


class TelegramChannel(NotificationChannel):
    name: ClassVar[str] = 'telegram'
    label: ClassVar[str] = 'Telegram'
    icon: ClassVar[str] = '✈️'
    placeholder: ClassVar[str] = 'https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>'
    allowed_domains: ClassVar[tuple[str, ...]] = ('api.telegram.org',)

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        domain = cls._extract_domain(webhook)
        return any(domain.endswith(d) for d in cls.allowed_domains)

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: Optional[str] = None) -> bool:
        text = f"*{title}*\n{content}"
        if url:
            text += f"\n[查看详情]({url})"
        payload = json.dumps({
            'text': text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status == 200
        except (URLError, HTTPError, Exception):
            return False
