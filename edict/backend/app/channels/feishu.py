import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import ClassVar, Optional

from .base import NotificationChannel


class FeishuChannel(NotificationChannel):
    name: ClassVar[str] = 'feishu'
    label: ClassVar[str] = '飞书 Feishu'
    icon: ClassVar[str] = '💬'
    placeholder: ClassVar[str] = 'https://open.feishu.cn/open-apis/bot/v2/hook/...'
    allowed_domains: ClassVar[tuple[str, ...]] = ('open.feishu.cn', 'open.larksuite.com')

    @classmethod
    def validate_webhook(cls, webhook: str) -> bool:
        if not cls._validate_url_scheme(webhook):
            return False
        domain = cls._extract_domain(webhook)
        return any(domain.endswith(d) for d in cls.allowed_domains)

    @classmethod
    def send(cls, webhook: str, title: str, content: str, url: Optional[str] = None) -> bool:
        elements = [
            {'tag': 'div', 'text': {'tag': 'lark_md', 'content': content}}
        ]
        if url:
            elements.append({
                'tag': 'action',
                'actions': [{
                    'tag': 'button',
                    'text': {'tag': 'plain_text', 'content': '查看详情'},
                    'url': url,
                    'type': 'primary'
                }]
            })
        payload = json.dumps({
            'msg_type': 'interactive',
            'card': {
                'header': {
                    'title': {'tag': 'plain_text', 'content': title},
                    'template': 'blue'
                },
                'elements': elements
            }
        }).encode()
        try:
            req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            resp = urlopen(req, timeout=10)
            return resp.status == 200
        except (URLError, HTTPError, Exception):
            return False