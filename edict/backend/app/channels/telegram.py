import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar

from .base import NotificationChannel


class TelegramChannel(NotificationChannel):
    name: ClassVar[str] = 'telegram'
    label: ClassVar[str] = 'Telegram'
    icon: ClassVar[str] = '✈️'
    placeholder: ClassVar[str] = 'https://api.telegram.org/bot<TOKEN>/sendMessage'
    allowed_domains: ClassVar[tuple[str, ...]] = ('api.telegram.org',)

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        domain = cls._extract_domain(webhook)
        return domain in cls.allowed_domains and '/bot' in webhook

    @classmethod
    def _escape_markdown(cls, text: str) -> str:
        escape_chars = '_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{c}' if c in escape_chars else c for c in text)

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        text = f"*{cls._escape_markdown(title)}*\n\n{cls._escape_markdown(content)}"
        if url:
            text += f"\n\n[查看详情]({url})"
        params = {
            'chat_id': cls._extract_chat_id(webhook),
            'text': text,
            'parse_mode': 'MarkdownV2',
            'disable_web_page_preview': True
        }
        full_url = webhook
        payload = json.dumps(params).encode()
        try:
            req = Request(full_url, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status == 200
        except (URLError, HTTPError, Exception):
            return False

    @classmethod
    def _extract_chat_id(cls, webhook: str) -> str:
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(webhook)
        params = parse_qs(parsed.query)
        if 'chat_id' in params:
            return params['chat_id'][0]
        return ''