"""Authority and bounded metadata for resumable upload sessions."""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass

from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.services.idempotency import IdempotencyService, SecretHmac
from tara_web.storage.layout import StorageLayout
from tara_web.storage.uploads import unlink_upload

_HEX = re.compile(r"^[0-9a-f]{64}$")
_BIDI = {chr(value) for value in range(0x202A, 0x202F)} | {
    chr(value) for value in range(0x2066, 0x206A)
}


class UploadUnauthorized(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedSession:
    session_id: str
    secret: str
    revision: int = 1


class UploadSessionService:
    def __init__(
        self,
        repository: AudioUploadRepository,
        layout: StorageLayout,
        hmac_service: SecretHmac,
        *,
        max_files: int = 1000,
        chunk_size: int = 1_048_576,
        max_sessions: int = 100,
        max_reserved_bytes: int = 10_737_418_240,
        max_upload_bytes: int = 1_073_741_824,
        max_merged_transcription_bytes: int = 32 * 1024 * 1024,
        retention_hours: int = 24,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self.repository, self.layout, self.hmac = repository, layout, hmac_service
        self.max_files, self.chunk_size = max_files, chunk_size
        self.max_sessions = max_sessions
        self.max_reserved_bytes = max_reserved_bytes
        self.max_upload_bytes = max_upload_bytes
        self.max_merged_transcription_bytes = max_merged_transcription_bytes
        if not 1 <= retention_hours <= 168:
            raise ValueError("upload session retention is invalid")
        self.retention_hours = retention_hours
        self.idempotency = idempotency

    def create(
        self, idempotency_key: str | None = None, *, input_type: str = "audio"
    ) -> CreatedSession:
        if input_type not in {"audio", "merged_transcription", "zip"}:
            raise ValueError("upload input type is invalid")
        if idempotency_key is not None and self.idempotency is not None:
            secret = self.hmac.derive_secret(idempotency_key, "upload-session-replay")

            def create() -> dict[str, object]:
                session_id = new_opaque_id("us")
                self.repository.create_session(
                    session_id,
                    self.hmac.digest(secret, "upload-secret"),
                    max_sessions=self.max_sessions,
                    input_type=input_type,
                    expires_hours=self.retention_hours,
                )
                return {"session_id": session_id, "revision": 1}

            result = self.idempotency.transactional_execute(
                "create_upload_session",
                "public_upload",
                idempotency_key,
                {"input_type": input_type},
                create,
            )
            return CreatedSession(
                str(result["session_id"]), secret, int(result["revision"])
            )
        session_id = new_opaque_id("us")
        secret = (
            self.hmac.derive_secret(idempotency_key, "upload-session-replay")
            if idempotency_key is not None
            else secrets.token_urlsafe(32)
        )
        self.repository.create_session(
            session_id,
            self.hmac.digest(secret, "upload-secret"),
            max_sessions=self.max_sessions,
            input_type=input_type,
            expires_hours=self.retention_hours,
        )
        return CreatedSession(session_id, secret)

    def authorize(
        self, session_id: str, secret: str | None, *, mutable: bool = False
    ) -> dict[str, object]:
        row = self.repository.session(session_id)
        if (
            row is None
            or not secret
            or not self.hmac.verify(secret, str(row["secret_hmac"]), "upload-secret")
            or not self.repository.session_is_readable(row)
            or (mutable and not self.repository.session_is_mutable(row))
        ):
            raise UploadUnauthorized("upload resource unavailable")
        return row

    def declare_file(
        self,
        session_id: str,
        secret: str | None,
        *,
        filename: str,
        size: int,
        sha256_hex: str,
        mime: str | None,
        replacement_for: str | None = None,
    ) -> dict[str, object]:
        session = self.authorize(session_id, secret, mutable=True)
        input_type = "zip" if session["archive_mode"] else str(session["input_type"])
        maximum = (
            self.max_merged_transcription_bytes
            if input_type == "merged_transcription"
            else self.max_upload_bytes
        )
        if not 1 <= size <= maximum or not _HEX.fullmatch(sha256_hex):
            raise ValueError("upload declaration is invalid")
        display = sanitize_display_name(filename)
        extension = display.rsplit(".", 1)[-1].lower() if "." in display else ""
        allowed_extensions = (
            {"yaml", "yml"}
            if input_type == "merged_transcription"
            else ({"zip"} if input_type == "zip" else {"mp3", "ogg"})
        )
        if extension not in allowed_extensions:
            raise ValueError("upload type is invalid")
        allowed_mimes = (
            {None, "application/yaml", "application/x-yaml", "text/yaml", "text/plain"}
            if input_type == "merged_transcription"
            else (
                {None, "application/zip", "application/x-zip-compressed"}
                if input_type == "zip"
                else {None, "audio/mpeg", "audio/mp3", "audio/ogg", "application/ogg"}
            )
        )
        if mime not in allowed_mimes:
            raise ValueError("upload type is invalid")
        file_id = new_opaque_id("uf")
        path = self.layout.upload_file(session_id, file_id)
        person = (
            sanitize_person(display.rsplit(".", 1)[0])
            if input_type == "audio"
            else None
        )
        self.repository.create_file(
            session_id=session_id,
            public_id=file_id,
            path=path.relative_path,
            name=display,
            person=person,
            size=size,
            digest=sha256_hex,
            mime=mime,
            chunk_size=self.chunk_size,
            max_files=self.max_files,
            max_reserved_bytes=self.max_reserved_bytes,
            replacement_for=replacement_for,
        )
        return {
            "file_id": file_id,
            "revision": 1,
            "confirmed_offset": 0,
            "chunk_size": self.chunk_size,
            "person": person,
        }

    def delete_file(
        self,
        session_id: str,
        file_id: str,
        secret: str | None,
        expected_revision: int,
    ) -> int:
        self.authorize(session_id, secret, mutable=True)
        row = self.repository.file_for_session(session_id, file_id)
        if row is None:
            raise UploadUnauthorized("upload resource unavailable")
        revision = self.repository.delete_file(session_id, file_id, expected_revision)
        upload = self.layout.parse_upload_path(str(row["storage_path"]))
        unlink_upload(self.layout, upload)
        return revision

    def cancel(
        self, session_id: str, secret: str | None, expected_revision: int
    ) -> int:
        self.authorize(session_id, secret, mutable=True)
        files = self.repository.files_for_session(session_id, limit=self.max_files)
        revision = self.repository.cancel_session(session_id, expected_revision)
        for row in files:
            upload = self.layout.parse_upload_path(str(row["storage_path"]))
            unlink_upload(self.layout, upload)
        return revision


def sanitize_display_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 255
        or any(
            ord(char) == 0x7F
            or char in _BIDI
            or unicodedata.category(char) in {"Cc", "Cf", "Cs", "Cn"}
            or char in {"/", "\\"}
            for char in normalized
        )
    ):
        raise ValueError("upload name is invalid")
    return normalized


def sanitize_person(value: str) -> str:
    result = sanitize_display_name(value)
    if len(result.encode("utf-8")) > 255:
        raise ValueError("person is invalid")
    return result


def new_opaque_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"
