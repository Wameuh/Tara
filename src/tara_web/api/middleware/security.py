"""Security headers and one common browser-origin guard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from fastapi import Request, Response

from tara_web.api.problem_details import problem


def canonical_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
        host = parsed.hostname.lower()
        default_port = 80 if parsed.scheme == "http" else 443
        authority = host if port in {None, default_port} else f"{host}:{port}"
        return f"{parsed.scheme}://{authority}"
    except ValueError:
        return None


async def secure_api(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    origins: set[str],
    public_https: bool,
    trusted_proxy_networks: tuple[str, ...],
    hsts_max_age_seconds: int,
    hsts_include_subdomains: bool,
) -> Response:
    if request.url.path.startswith("/api/v1") and request.method in {
        "POST",
        "PATCH",
        "PUT",
        "DELETE",
    }:
        origin = request.headers.get("origin")
        if origin and canonical_origin(origin) not in origins:
            return problem(request, 403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "object-src 'none'; form-action 'self'; script-src 'self' "
        "'wasm-unsafe-eval'; worker-src 'self'",
    )
    if hsts_max_age_seconds > 0 and _external_https(
        request, public_https, trusted_proxy_networks
    ):
        value = f"max-age={hsts_max_age_seconds}"
        if hsts_include_subdomains:
            value += "; includeSubDomains"
        response.headers.setdefault("Strict-Transport-Security", value)
    if request.url.path.startswith("/api/v1"):
        response.headers["Cache-Control"] = "no-store"
    return response


def client_identity(request: Request, trusted_proxy_networks: tuple[str, ...]) -> str:
    if not request.client:
        return "unknown"
    peer = request.client.host
    try:
        trusted = any(
            ip_address(peer) in ip_network(item) for item in trusted_proxy_networks
        )
    except ValueError:
        return "unknown"
    xff = request.headers.get("x-forwarded-for", "")
    if not trusted or not xff or len(xff) > 512:
        return peer if len(peer) <= 64 else "unknown"
    hops = [item.strip() for item in xff.split(",")]
    if not 1 <= len(hops) <= 8:
        return "unknown"
    try:
        addresses = [ip_address(item) for item in hops]
    except ValueError:
        return "unknown"
    for address in reversed(addresses):
        if not any(address in ip_network(item) for item in trusted_proxy_networks):
            return str(address)
    return peer if len(peer) <= 64 else "unknown"


def _external_https(
    request: Request, public_https: bool, trusted_proxy_networks: tuple[str, ...]
) -> bool:
    if request.url.scheme == "https":
        return True
    if not public_https or not request.client or not trusted_proxy_networks:
        return False
    try:
        peer = ip_address(request.client.host)
    except ValueError:
        return False
    if not any(peer in ip_network(item) for item in trusted_proxy_networks):
        return False
    values = request.headers.get("x-forwarded-proto", "").split(",")
    return len(values) == 1 and values[0].strip().lower() == "https"
