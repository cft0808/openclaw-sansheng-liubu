import socket

from edict.backend.app.channels.webhook import WebhookChannel


def _fake_getaddrinfo(*addresses):
    def fake(host, port, type=0, *args, **kwargs):
        family = socket.AF_INET6 if ':' in addresses[0] else socket.AF_INET
        return [
            (family, type or socket.SOCK_STREAM, 6, '', (address, port))
            for address in addresses
        ]

    return fake


def test_generic_webhook_requires_https(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('8.8.8.8'))

    assert WebhookChannel.validate_webhook('http://example.com/webhook') is False


def test_generic_webhook_rejects_loopback_ip(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('127.0.0.1'))

    assert WebhookChannel.validate_webhook('https://127.0.0.1/webhook') is False


def test_generic_webhook_rejects_private_dns_result(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('10.0.0.12'))

    assert WebhookChannel.validate_webhook('https://hooks.example.test/webhook') is False


def test_generic_webhook_rejects_mixed_public_and_private_dns_results(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('8.8.8.8', '192.168.1.20'))

    assert WebhookChannel.validate_webhook('https://hooks.example.test/webhook') is False


def test_generic_webhook_accepts_public_dns_result(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('8.8.8.8'))

    assert WebhookChannel.validate_webhook('https://hooks.example.test/webhook') is True


def test_generic_webhook_send_revalidates_destination(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', _fake_getaddrinfo('127.0.0.1'))

    called = False

    def fake_urlopen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError('urlopen should not run for a blocked destination')

    monkeypatch.setattr('edict.backend.app.channels.webhook.urlopen', fake_urlopen)

    assert WebhookChannel.send('https://hooks.example.test/webhook', 'title', 'content') is False
    assert called is False
