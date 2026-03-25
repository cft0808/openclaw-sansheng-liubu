from typing import Protocol, ClassVar
from abc import abstractmethod


class NotificationChannel(Protocol):
    name: ClassVar[str]
    label: ClassVar[str]
    icon: ClassVar[str]
    placeholder: ClassVar[str]
    allowed_domains: ClassVar[tuple[str, ...]]

    @classmethod
    @abstractmethod
    def validate_webhook(cls, webhook: str) -> bool:
        ...

    @classmethod
    @abstractmethod
    def send(cls, webhook: str, title: str, content: str, url: str | None = None) -> bool:
        ...

    @classmethod
    def _validate_url_scheme(cls, url: str) -> bool:
        return url.startswith('https://')

    @classmethod
    def _extract_domain(cls, url: str) -> str:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return ''