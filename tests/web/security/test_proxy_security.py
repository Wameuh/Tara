from __future__ import annotations

from types import SimpleNamespace

from tara_web.api.middleware.security import _external_https, client_identity


def _request(peer: str, **headers: str) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers=headers,
        url=SimpleNamespace(scheme="http"),
    )


def test_client_identity_proxy_matrix() -> None:
    trusted = ("10.0.0.0/24", "2001:db8::/32")
    assert (
        client_identity(
            _request("198.51.100.2", **{"x-forwarded-for": "1.2.3.4"}), trusted
        )
        == "198.51.100.2"
    )
    assert (
        client_identity(
            _request("10.0.0.2", **{"x-forwarded-for": "203.0.113.1, 10.0.0.3"}),
            trusted,
        )
        == "203.0.113.1"
    )
    assert (
        client_identity(
            _request("10.0.0.2", **{"x-forwarded-for": "10.0.0.3"}), trusted
        )
        == "10.0.0.2"
    )
    assert (
        client_identity(_request("10.0.0.2", **{"x-forwarded-for": "bad"}), trusted)
        == "unknown"
    )
    assert (
        client_identity(
            _request("2001:db8::2", **{"x-forwarded-for": "2001:db9::1"}), trusted
        )
        == "2001:db9::1"
    )


def test_external_https_requires_a_trusted_attestation() -> None:
    trusted = ("10.0.0.0/24",)
    assert not _external_https(
        _request("198.51.100.2", **{"x-forwarded-proto": "https"}), True, trusted
    )
    assert _external_https(
        _request("10.0.0.2", **{"x-forwarded-proto": "https"}), True, trusted
    )
    assert not _external_https(
        _request("10.0.0.2", **{"x-forwarded-proto": "http, https"}),
        True,
        trusted,
    )
    direct = _request("198.51.100.2")
    direct.url.scheme = "https"
    assert _external_https(direct, False, ())
