"""Versioned canonical request fingerprints and durable idempotency reservations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tara_web.db.connection import ConnectionFactory, DatabaseConflict

CANONICAL_VERSION = "v1"
MAX_TTL = timedelta(days=30)


class IdempotencyConflict(DatabaseConflict):
    """A key has already been used for another canonical request."""


def canonicalize(value: Any) -> bytes:
    """Produce a bounded deterministic JSON representation, rejecting exotic values."""
    _validate_value(value)
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    if len(encoded) > 65_536:
        raise ValueError("idempotency payload is too large")
    return CANONICAL_VERSION.encode() + b":" + encoded


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


def _validate_value(value: Any, *, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("idempotency payload is too deep")
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("idempotency string is too large")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, list | tuple):
        if len(value) > 256:
            raise ValueError("idempotency payload has too many items")
        for item in value:
            _validate_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) for key in value):
            raise ValueError("idempotency payload is invalid")
        for key, item in value.items():
            _validate_value(key, depth=depth + 1)
            _validate_value(item, depth=depth + 1)
        return
    raise ValueError("idempotency payload is invalid")


class SecretHmac:
    """Purpose-bound HMACs; values are never stored or compared in clear text."""

    def __init__(
        self, key: bytes, *, version: int = 1, previous: dict[int, bytes] | None = None
    ) -> None:
        if previous and (version in previous or len(previous) > 4):
            raise ValueError("HMAC keyring has duplicate versions")
        keyring = {version: key, **(previous or {})}
        if any(
            not isinstance(item, bytes) or version < 1 or len(item) < 32
            for version, item in keyring.items()
        ):
            raise ValueError("HMAC keyring is invalid")
        self._keys, self._version = keyring, version

    def digest(self, value: str, purpose: str) -> str:
        raw = hmac.new(
            self._keys[self._version],
            f"v{self._version}:{purpose}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"v{self._version}:{raw}"

    def verify(self, value: str, stored: str, purpose: str) -> bool:
        prefix, separator, _ = stored.partition(":")
        if not separator or not prefix.startswith("v"):
            return False
        try:
            version = int(prefix[1:])
        except ValueError:
            return False
        key = self._keys.get(version)
        if key is None:
            return False
        candidate = hmac.new(
            key, f"v{version}:{purpose}:{value}".encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"v{version}:{candidate}", stored)

    def candidate_digests(self, value: str, purpose: str) -> tuple[str, ...]:
        """Return current then previous version digests, with a bounded keyring."""
        versions = (self._version, *sorted(set(self._keys) - {self._version}))
        return tuple(self._digest_for(version, value, purpose) for version in versions)

    def stable_digest(self, value: str, purpose: str) -> str:
        """Keep admission identities stable while an old HMAC key is retained."""
        return self._digest_for(min(self._keys), value, purpose)

    @property
    def current_version(self) -> int:
        return self._version

    def derive_secret(
        self, value: str, purpose: str, *, version: int | None = None
    ) -> str:
        """Derive a URL-safe opaque secret without persisting recoverable data."""
        selected = self._version if version is None else version
        key = self._keys.get(selected)
        if key is None:
            raise ValueError("HMAC derivation version is unavailable")
        raw = hmac.new(
            key,
            f"v{selected}:{purpose}:{value}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _digest_for(self, version: int, value: str, purpose: str) -> str:
        raw = hmac.new(
            self._keys[version],
            f"v{version}:{purpose}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"v{version}:{raw}"


@dataclass(frozen=True)
class Reservation:
    completed_result: dict[str, Any] | None
    in_progress: bool
    acquired: bool = False


class IdempotencyService:
    def __init__(self, factory: ConnectionFactory, hmac_service: SecretHmac) -> None:
        self._factory, self._hmac = factory, hmac_service

    def reserve(
        self,
        operation: str,
        owner: str,
        key: str,
        payload: Any,
        *,
        ttl: timedelta = timedelta(days=7),
    ) -> Reservation:
        self._validate_request(operation, owner, key)
        if not ttl > timedelta() or ttl > MAX_TTL:
            raise ValueError("invalid idempotency key")
        request_hash = fingerprint(payload)
        now = datetime.now(UTC)
        with self._factory.transaction() as connection:
            connection.execute(
                "DELETE FROM idempotency_keys WHERE expires_at <= ?", (now.isoformat(),)
            )
            row = self._find(connection, operation, owner, key)
            if row:
                if not hmac.compare_digest(row["request_fingerprint"], request_hash):
                    raise IdempotencyConflict("idempotency key payload conflict")
                return Reservation(
                    json.loads(row["result_json"]) if row["result_json"] else None,
                    row["result_json"] is None,
                    False,
                )
            connection.execute(
                "INSERT INTO idempotency_keys(operation, owner_hmac, key_hmac, "
                "request_fingerprint, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    operation,
                    self._hmac.digest(owner, "owner"),
                    self._hmac.digest(key, "idempotency"),
                    request_hash,
                    now.isoformat(),
                    (now + ttl).isoformat(),
                ),
            )
            return Reservation(None, True, True)

    def transactional_execute(
        self,
        operation: str,
        owner: str,
        key: str,
        payload: Any,
        callback: Callable[[Any], dict[str, Any]] | Callable[[], dict[str, Any]],
        *,
        ttl: timedelta = timedelta(days=7),
    ) -> dict[str, Any]:
        """Atomically reserve, apply one SQLite mutation, and store its result."""
        self._validate_request(operation, owner, key)
        if not timedelta() < ttl <= MAX_TTL:
            raise ValueError("invalid idempotency key")
        request_hash = fingerprint(payload)
        with self._factory.transaction() as connection:
            now = datetime.now(UTC)
            connection.execute(
                "DELETE FROM idempotency_keys WHERE expires_at <= ?", (now.isoformat(),)
            )
            row = self._find(connection, operation, owner, key)
            if row is not None:
                if not hmac.compare_digest(row["request_fingerprint"], request_hash):
                    raise IdempotencyConflict("idempotency key payload conflict")
                if row["result_json"] is not None:
                    return json.loads(row["result_json"])
                # Older interrupted reservations had no atomic effect/result pair.
                connection.execute(
                    "DELETE FROM idempotency_keys WHERE id=?", (row["id"],)
                )
            connection.execute(
                "INSERT INTO idempotency_keys(operation,owner_hmac,key_hmac,"
                "request_fingerprint,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                (
                    operation,
                    self._hmac.digest(owner, "owner"),
                    self._hmac.digest(key, "idempotency"),
                    request_hash,
                    now.isoformat(),
                    (now + ttl).isoformat(),
                ),
            )
            # New callers receive the owning transaction. Legacy zero-argument
            # callbacks remain supported while upload routes migrate gradually.
            try:
                result = callback(connection)  # type: ignore[call-arg]
            except TypeError as exc:
                if "positional" not in str(exc):
                    raise
                result = callback()  # type: ignore[call-arg]
            if not isinstance(result, dict):
                raise ValueError("idempotency result is invalid")
            _validate_value(result)
            serialized = json.dumps(
                result, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            if len(serialized) > 65_536:
                raise ValueError("idempotency result is too large")
            connection.execute(
                "UPDATE idempotency_keys SET result_json=?,completed_at=? "
                "WHERE operation=? AND owner_hmac=? AND key_hmac=? "
                "AND result_json IS NULL",
                (
                    serialized,
                    now.isoformat(),
                    operation,
                    self._hmac.digest(owner, "owner"),
                    self._hmac.digest(key, "idempotency"),
                ),
            )
            return result

    def rotation_replay_authorized(self, *, owner: str, key: str, secret: str) -> bool:
        """Authenticate a lost rotation response without retaining its secret."""
        connection = self._factory.connect()
        try:
            row = self._find(connection, "rotate_job_secret", owner, key)
            if row is None or row["result_json"] is None:
                return False
            result = json.loads(row["result_json"])
            proof = result.get("authorization_proof")
            return isinstance(proof, str) and hmac.compare_digest(
                proof, self._hmac.digest(secret, "rotation-replay")
            )
        finally:
            connection.close()

    def complete(
        self, operation: str, owner: str, key: str, result: dict[str, Any]
    ) -> None:
        if not isinstance(result, dict):
            raise ValueError("idempotency result is invalid")
        self._validate_request(operation, owner, key)
        _validate_value(result)
        serialized = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(serialized) > 65_536:
            raise ValueError("idempotency result is too large")
        with self._factory.transaction() as connection:
            now = datetime.now(UTC).isoformat()
            row = self._find(connection, operation, owner, key)
            cursor = connection.execute(
                "UPDATE idempotency_keys SET result_json = ?, completed_at = ? "
                "WHERE id = ? AND result_json IS NULL AND expires_at > ?",
                (serialized, now, row["id"] if row else -1, now),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflict("idempotency reservation is unavailable")

    def _find(self, connection: Any, operation: str, owner: str, key: str) -> Any:
        owners = self._hmac.candidate_digests(owner, "owner")
        keys = self._hmac.candidate_digests(key, "idempotency")
        pairs = tuple(zip(owners, keys, strict=True))
        clauses = " OR ".join("(owner_hmac = ? AND key_hmac = ?)" for _ in pairs)
        parameters = tuple(item for pair in pairs for item in pair)
        return connection.execute(
            "SELECT id, request_fingerprint, result_json FROM idempotency_keys "
            f"WHERE operation = ? AND ({clauses}) ORDER BY id ASC LIMIT 1",
            (operation, *parameters),
        ).fetchone()

    @staticmethod
    def _validate_request(operation: str, owner: str, key: str) -> None:
        if (
            not 1 <= len(operation) <= 64
            or not 1 <= len(owner) <= 256
            or not 1 <= len(key) <= 256
        ):
            raise ValueError("invalid idempotency key")
