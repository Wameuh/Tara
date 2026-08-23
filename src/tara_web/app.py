"""FastAPI application factory and deterministic process lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from tara.web_contracts import RunnerLimits

from .api.dependencies.auth import InvalidSecretRateLimited
from .api.middleware.access_log import log_access
from .api.middleware.correlation import correlate
from .api.middleware.rate_limit import RateLimiter
from .api.middleware.security import canonical_origin, client_identity, secure_api
from .api.problem_details import problem
from .api.router import router as api_router
from .catalogs import validate_catalogues
from .config import RuntimeConfig, public_runtime_config
from .db.connection import ConnectionFactory, DatabaseError
from .db.migrations import migrate
from .db.repositories.artifacts import ArtifactRepository
from .db.repositories.audio_uploads import AudioUploadRepository
from .estimation.service import EstimationService
from .lifecycle.shutdown import controlled_backup, controlled_drain
from .lifecycle.startup import verify_database
from .orchestration.job_service import JobService
from .orchestration.job_workspace import JobWorkspaceService
from .orchestration.scheduler import Scheduler
from .realtime.broker import EventBroker
from .services.budget import BudgetService
from .services.chunk_upload import ChunkUploadService
from .services.circuit_breaker import CircuitBreaker
from .services.idempotency import IdempotencyService, SecretHmac
from .services.input_validation import UploadValidationRunner, ValidationPolicy
from .services.merged_transcription_validation import (
    MergedTranscriptionValidationRunner,
    MergedValidationPolicy,
)
from .services.relaunch import RelaunchService
from .services.upload_maintenance import drain_startup_uploads, maintain_uploads
from .services.upload_sessions import UploadSessionService
from .services.validation_scheduler import ValidationScheduler
from .services.zip_archive_validation import (
    ZipArchiveValidationRunner,
    ZipValidationPolicy,
)
from .services.zip_validation import ZipPolicy
from .storage.artifacts import ArtifactPolicy, ArtifactService, validate_final_yaml_v1
from .storage.cleanup import (
    OrphanScanner,
    cleanup_expired,
    cleanup_expired_inputs,
    cleanup_orphans,
    expire_job_metadata,
)
from .storage.layout import StorageError, StorageLayout
from .storage.reconciliation import reconcile_all

LOGGER = logging.getLogger(__name__)


def create_app(config: RuntimeConfig, frontend_dist: Path | None = None) -> FastAPI:
    """Create an isolated application instance. No I/O happens before lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        app.state.draining = False
        storage = config.web.storage
        for path in (storage.root, storage.backups_root, storage.sqlite_path.parent):
            _create_private_directory(path)
        permission_paths = {
            storage.root,
            storage.backups_root,
            storage.sqlite_path.parent,
        }
        _validate_sensitive_permissions(*permission_paths)
        database = ConnectionFactory(storage.sqlite_path, storage.root)
        connection = database.connect()
        try:
            migrate(
                connection,
                database_path=storage.sqlite_path,
                backups_root=storage.backups_root,
            )
            verify_database(connection)
            layout = StorageLayout(storage.root)
            artifacts = ArtifactRepository(database)
            policy = ArtifactPolicy(
                max_bytes=config.web.limits.max_artifact_bytes,
                minimum_free_bytes=config.web.limits.min_free_storage_bytes,
                max_per_job=config.web.limits.max_artifacts_per_job,
                cleanup_batch_size=config.web.limits.storage_cleanup_batch_size,
                orphan_grace_seconds=config.web.limits.storage_orphan_grace_seconds,
            )
            reconcile_all(
                layout,
                artifacts,
                limit=policy.cleanup_batch_size,
                max_bytes=policy.max_bytes,
                validator=validate_final_yaml_v1,
            )
            initial_scanner = OrphanScanner(layout)
            try:
                try:
                    await asyncio.to_thread(
                        _storage_maintenance_cycle,
                        layout,
                        artifacts,
                        policy,
                        initial_scanner,
                    )
                except Exception:
                    LOGGER.exception("storage_maintenance_failed")
            finally:
                initial_scanner.close()
            maintenance = asyncio.create_task(
                _storage_maintenance(
                    layout,
                    artifacts,
                    policy,
                    config.web.limits.storage_maintenance_interval_seconds,
                )
            )
            languages = validate_catalogues(
                Path(__file__).parent / "i18n_manifest.json",
                config.web.supported_languages,
                config.web.default_language,
            )
            # Kept for the stage-01 health contract; repositories use factory rows.
            connection.row_factory = None
            app.state.sqlite = connection
            app.state.database = database
            app.state.storage_layout = layout
            app.state.artifact_policy = policy
            audio_uploads = AudioUploadRepository(database)
            upload_key = _load_upload_hmac_key(layout.root, config)
            upload_hmac = SecretHmac(upload_key)
            upload_sessions = UploadSessionService(
                audio_uploads,
                layout,
                upload_hmac,
                max_files=config.web.limits.max_upload_files,
                chunk_size=config.web.limits.recommended_chunk_bytes,
                max_sessions=config.web.limits.max_upload_sessions,
                max_reserved_bytes=config.web.limits.max_reserved_upload_bytes,
                max_upload_bytes=config.web.limits.max_upload_bytes,
                max_merged_transcription_bytes=(
                    config.web.limits.max_merged_transcription_bytes
                ),
                retention_hours=config.web.limits.upload_session_retention_hours,
                idempotency=IdempotencyService(database, upload_hmac),
            )
            app.state.audio_upload_repository = audio_uploads
            app.state.artifact_repository = artifacts
            app.state.upload_sessions = upload_sessions
            app.state.relaunch_service = RelaunchService(layout)
            app.state.chunk_upload = ChunkUploadService(
                upload_sessions,
                audio_uploads,
                layout,
                max_chunk_bytes=config.web.limits.max_chunk_bytes,
                minimum_free_bytes=config.web.limits.min_free_storage_bytes,
            )
            await asyncio.to_thread(
                drain_startup_uploads,
                audio_uploads,
                layout,
                batch_size=config.web.limits.storage_cleanup_batch_size,
            )
            audio_uploads.requeue_interrupted_validations()
            upload_maintenance = asyncio.create_task(
                _upload_maintenance(
                    audio_uploads,
                    layout,
                    config.web.limits.storage_cleanup_batch_size,
                    config.web.limits.storage_maintenance_interval_seconds,
                )
            )
            validation_scheduler = ValidationScheduler(
                config.web.limits.validation_concurrency
            )
            validation_scheduler.start()
            validation_runner = UploadValidationRunner(
                audio_uploads,
                layout,
                ValidationPolicy(
                    ffprobe_timeout=config.web.limits.ffprobe_timeout_seconds,
                    ffmpeg_timeout=config.web.limits.ffmpeg_timeout_seconds,
                ),
            )
            merged_validation_runner = MergedTranscriptionValidationRunner(
                audio_uploads,
                layout,
                MergedValidationPolicy(
                    max_bytes=config.web.limits.max_merged_transcription_bytes,
                    max_tokens=config.web.limits.max_merged_transcription_tokens,
                    timeout_seconds=(
                        config.web.limits.merged_validation_timeout_seconds
                    ),
                    memory_bytes=config.web.limits.merged_validation_memory_bytes,
                    max_depth=config.web.limits.merged_yaml_max_depth,
                    max_nodes=config.web.limits.merged_yaml_max_nodes,
                    max_scalar_chars=config.web.limits.merged_yaml_max_scalar_chars,
                    max_aliases=config.web.limits.merged_yaml_max_aliases,
                ),
            )
            zip_validation_runner = ZipArchiveValidationRunner(
                audio_uploads,
                layout,
                ZipValidationPolicy(
                    archive=ZipPolicy(
                        max_archive_bytes=config.web.limits.max_upload_bytes,
                        max_entries=config.web.limits.zip_max_entries,
                        max_uncompressed_bytes=(
                            config.web.limits.zip_max_uncompressed_bytes
                        ),
                        max_compression_ratio=(
                            config.web.limits.zip_max_compression_ratio
                        ),
                        max_depth=config.web.limits.zip_max_depth,
                        timeout_seconds=(
                            config.web.limits.zip_validation_timeout_seconds
                        ),
                    ),
                    ffprobe_timeout=config.web.limits.ffprobe_timeout_seconds,
                    ffmpeg_timeout=config.web.limits.ffmpeg_timeout_seconds,
                ),
            )
            validation_wakeup = asyncio.Event()
            app.state.upload_validation_wakeup = validation_wakeup
            app.state.loop = asyncio.get_running_loop()
            validation_dispatch = asyncio.create_task(
                _dispatch_upload_validations(
                    audio_uploads,
                    validation_scheduler,
                    validation_runner,
                    merged_validation_runner,
                    zip_validation_runner,
                    validation_wakeup,
                )
            )
            event_broker = EventBroker(
                maximum=config.web.limits.max_sse_connections,
                per_job=config.web.limits.max_sse_connections_per_job,
            )

            def notify_job_change(job_id: str) -> None:
                event_connection = database.connect()
                try:
                    row = event_connection.execute(
                        "SELECT revision FROM jobs WHERE public_id=?", (job_id,)
                    ).fetchone()
                finally:
                    event_connection.close()
                if row is not None:
                    app.state.loop.call_soon_threadsafe(
                        event_broker.publish,
                        job_id,
                        {
                            "type": "snapshot_updated",
                            "revision": int(row["revision"]),
                            "data": {},
                        },
                    )

            artifact_service = ArtifactService(layout, artifacts, policy)
            budget_service = BudgetService(
                database,
                ceiling_micro_eur=(
                    config.web.limits.inference_budget_micro_eur
                    if config.web.runner_mode == "tara"
                    else None
                ),
                reservation_micro_eur=(
                    config.web.limits.inference_reservation_micro_eur
                ),
            )
            circuit_breaker = CircuitBreaker(
                database,
                failure_threshold=config.web.limits.circuit_failure_threshold,
                open_seconds=config.web.limits.circuit_open_seconds,
            )
            estimation_service = EstimationService(
                database,
                minimum_observations=(
                    config.web.limits.estimation_minimum_observations
                ),
                active_capacity=config.web.limits.max_active_jobs,
            )
            job_service = JobService(
                database,
                max_waiting_jobs=config.web.limits.max_waiting_jobs,
                artifact_service=artifact_service,
                workspace_service=JobWorkspaceService(
                    database, layout, artifact_service=artifact_service
                ),
                on_change=notify_job_change,
                tara_config_snapshot=config.tara_config_snapshot,
                runner_limits=RunnerLimits(
                    max_merged_transcription_tokens=(
                        config.web.limits.max_merged_transcription_tokens
                    )
                ),
                budget_service=budget_service,
                circuit_breaker=circuit_breaker,
                record_metrics=config.web.runner_mode == "tara",
            )
            scheduler = Scheduler(
                job_service,
                max_active_jobs=config.web.limits.max_active_jobs,
                ipc_maximum=config.web.limits.max_ipc_messages,
                job_timeout_seconds=config.web.limits.job_timeout_seconds,
                cancellation_grace_seconds=config.web.limits.cancellation_grace_seconds,
                runner_kind=config.web.runner_mode,
            )
            scheduler.start()
            app.state.job_service = job_service
            app.state.budget_service = budget_service
            app.state.circuit_breaker = circuit_breaker
            app.state.estimation_service = estimation_service
            app.state.job_scheduler = scheduler
            app.state.secret_hmac = upload_hmac
            app.state.idempotency = IdempotencyService(database, upload_hmac)
            app.state.event_broker = event_broker
            app.state.rate_limiter = RateLimiter(
                limits={
                    "global": config.web.limits.rate_limit_global,
                    "invalid_secret": config.web.limits.rate_limit_invalid_secret,
                    "public_creation": config.web.limits.rate_limit_public_creation,
                    "expensive_command": config.web.limits.rate_limit_expensive_command,
                    "polling": config.web.limits.rate_limit_polling,
                    "sse_open": config.web.limits.rate_limit_sse_open,
                    "upload_chunk": config.web.limits.rate_limit_upload_chunk,
                    "upload_chunk_global": (
                        config.web.limits.rate_limit_upload_chunk_global
                    ),
                },
                window_seconds=config.web.limits.rate_limit_window_seconds,
            )

            def publish_job_event(job_id: str, revision: int) -> None:
                app.state.loop.call_soon_threadsafe(
                    app.state.event_broker.publish,
                    job_id,
                    {
                        "type": "snapshot_updated",
                        "revision": revision,
                        "data": {},
                    },
                )

            app.state.publish_job_event = publish_job_event
            app.state.runtime_config = config
            app.state.public_config = public_runtime_config(config, languages)
            app.state.ready = True
            app.state.draining = False
            yield
        finally:
            app.state.ready = False
            app.state.draining = True
            if "scheduler" in locals():
                drain_result = await controlled_drain(
                    scheduler,
                    grace_seconds=config.web.timeouts.shutdown_grace_seconds,
                )
                LOGGER.info(
                    "scheduler_drained",
                    extra={
                        "completed": drain_result.completed,
                        "cancelled": drain_result.cancelled,
                    },
                )
            if "maintenance" in locals():
                maintenance.cancel()
                try:
                    await maintenance
                except asyncio.CancelledError:
                    pass
            if "validation_dispatch" in locals():
                validation_dispatch.cancel()
                try:
                    await validation_dispatch
                except asyncio.CancelledError:
                    pass
            if "upload_maintenance" in locals():
                upload_maintenance.cancel()
                try:
                    await upload_maintenance
                except asyncio.CancelledError:
                    pass
            if "validation_scheduler" in locals():
                await validation_scheduler.close()
            if "scheduler" in locals():
                await scheduler.close()
            if "layout" in locals() and "artifacts" in locals():
                try:
                    reconcile_all(
                        layout,
                        artifacts,
                        limit=policy.cleanup_batch_size,
                        max_bytes=policy.max_bytes,
                        validator=validate_final_yaml_v1,
                    )
                except (DatabaseError, StorageError, OSError):
                    LOGGER.exception("shutdown_storage_reconciliation_failed")
            connection.close()
            if (
                config.web.backup.enabled
                and "database" in locals()
                and "layout" in locals()
            ):
                key = config.backup_signing_key
                if key is None:
                    raise RuntimeError("controlled backup signing key is unavailable")
                result = await asyncio.to_thread(
                    controlled_backup,
                    database,
                    layout,
                    config.web.storage.backups_root,
                    key.get_secret_value().encode("utf-8"),
                )
                LOGGER.info(
                    "controlled_backup_created",
                    extra={"artifact_count": result.artifact_count},
                )

    docs_enabled = config.web.documentation.enabled
    app = FastAPI(
        title="Tara Web API",
        version="1.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(config.web.security.allowed_hosts)
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _exc: RequestValidationError
    ) -> Response:
        return problem(request, 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/v1"):
            return problem(request, exc.status_code)
        return Response(status_code=exc.status_code)

    @app.exception_handler(InvalidSecretRateLimited)
    async def invalid_secret_limited(
        request: Request, exc: InvalidSecretRateLimited
    ) -> Response:
        response = problem(request, 429)
        response.headers["Retry-After"] = str(min(60, max(1, int(exc.args[0]))))
        return response

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _exc: Exception) -> Response:
        if request.url.path.startswith("/api/v1"):
            return problem(request, 500)
        raise _exc

    @app.middleware("http")
    async def api_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origins = {
            canonical_origin(str(config.web.public_url)),
            *(
                canonical_origin(str(item))
                for item in config.web.security.allowed_origins
            ),
        }
        request.state.client_identity = client_identity(
            request, config.web.security.trusted_proxy_networks
        )
        if request.url.path.startswith("/api/v1"):
            category = _rate_category(request)
            retry = request.app.state.rate_limiter.allow(
                category,
                request.state.client_identity,
            )
            if retry is not None:
                response = problem(request, 429)
                response.headers["Retry-After"] = str(retry)
                return response
        return await secure_api(
            request,
            call_next,
            origins={item for item in origins if item},
            public_https=str(config.web.public_url).startswith("https://"),
            trusted_proxy_networks=config.web.security.trusted_proxy_networks,
            hsts_max_age_seconds=config.web.security.hsts_max_age_seconds,
            hsts_include_subdomains=config.web.security.hsts_include_subdomains,
        )

    @app.middleware("http")
    async def request_bounds(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/api/v1"):
            secret = request.headers.get("x-tara-job-secret")
            key = request.headers.get("idempotency-key")
            revision = request.headers.get("expected-revision")
            length = request.headers.get("content-length")
            if (
                (secret is not None and len(secret) > 512)
                or (key is not None and not 1 <= len(key) <= 256)
                or (
                    revision is not None
                    and (
                        not revision.isascii()
                        or not revision.isdecimal()
                        or int(revision) > 2_147_483_647
                    )
                )
                or (
                    length is not None
                    and (
                        not length.isdecimal()
                        or int(length) > config.web.limits.max_upload_bytes
                    )
                )
            ):
                return problem(request, 400)
            path = request.url.path
            method = request.method
            transfer = request.headers.get("transfer-encoding")
            content_type = request.headers.get("content-type", "").lower()
            bodyless = (
                path == "/api/v1/uploads/sessions"
                or path.endswith("/jobs")
                or (path.startswith("/api/v1/jobs/") and method in {"POST", "DELETE"})
            )
            json_route = method in {"POST", "PATCH"} and (
                path.endswith("/files")
                or path.endswith("/files/replace")
                or path.endswith("/person")
            )
            chunk_route = method == "PATCH" and path.endswith("/chunks")
            if (
                bodyless
                and method in {"POST", "DELETE"}
                and (transfer or length not in {None, "0"})
            ):
                return problem(request, 400)
            if json_route and (
                transfer
                or not (
                    content_type == "application/json"
                    or content_type == "application/json; charset=utf-8"
                )
                or length is None
                or int(length) > config.web.limits.max_json_body_bytes
            ):
                return problem(request, 400)
            if chunk_route and (
                content_type != "application/octet-stream"
                or length is None
                or int(length) < 1
                or int(length) > config.web.limits.max_chunk_bytes
            ):
                return problem(request, 400)
        return await call_next(request)

    @app.middleware("http")
    async def correlation(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await correlate(request, call_next)

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await log_access(request, call_next)

    if config.web.security.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                str(origin) for origin in config.web.security.allowed_origins
            ],
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=[
                "Accept",
                "Content-Type",
                "Idempotency-Key",
                "Expected-Revision",
                "Upload-Checksum",
                "Upload-Offset",
                "X-Tara-Job-Secret",
            ],
        )
    app.include_router(api_router)
    if frontend_dist and frontend_dist.is_dir():
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> FileResponse:
            candidate = frontend_dist / path
            if path == "api" or path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="API route not found")
            if (
                path
                and candidate.is_file()
                and candidate.resolve().is_relative_to(frontend_dist.resolve())
            ):
                return FileResponse(candidate)
            if path.startswith("assets/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="asset not found")
            return FileResponse(frontend_dist / "index.html")

    return app


def _validate_sensitive_permissions(*paths: Path) -> None:
    """Reject unsafe POSIX roots; Windows ACL verification is an operator concern."""
    if os.name == "nt":
        return
    for path in paths:
        if path.is_symlink():
            raise RuntimeError("sensitive path must not be a symbolic link")
        mode = path.stat().st_mode
        if mode & 0o077 or mode & 0o700 != 0o700:
            raise RuntimeError(f"sensitive path is group/world writable: {path}")


def _create_private_directory(path: Path) -> None:
    for component in (path.absolute(), *path.absolute().parents):
        if component.exists() and component.is_symlink():
            raise RuntimeError("sensitive path must not be a symbolic link")
        if component.parent == component:
            break
    if path.exists():
        return
    old_umask = os.umask(0o077) if os.name != "nt" else None
    try:
        path.mkdir(parents=True, mode=0o700)
    finally:
        if old_umask is not None:
            os.umask(old_umask)


async def _storage_maintenance(
    layout: StorageLayout,
    artifacts: ArtifactRepository,
    policy: ArtifactPolicy,
    interval_seconds: int,
) -> None:
    """Run a retention cycle periodically without taking the HTTP server down."""
    scanner = OrphanScanner(layout)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(
                    _storage_maintenance_cycle, layout, artifacts, policy, scanner
                )
            except Exception:
                LOGGER.exception("storage_maintenance_failed")
    finally:
        scanner.close()


def _storage_maintenance_cycle(
    layout: StorageLayout,
    artifacts: ArtifactRepository,
    policy: ArtifactPolicy,
    scanner: OrphanScanner,
) -> None:
    reconcile_all(
        layout,
        artifacts,
        limit=policy.cleanup_batch_size,
        max_bytes=policy.max_bytes,
        validator=validate_final_yaml_v1,
    )
    cleanup_expired(layout, artifacts, batch_size=policy.cleanup_batch_size)
    cleanup_expired_inputs(layout, artifacts, batch_size=policy.cleanup_batch_size)
    expire_job_metadata(artifacts, batch_size=policy.cleanup_batch_size)
    cleanup_orphans(
        layout,
        artifacts,
        grace_seconds=policy.orphan_grace_seconds,
        batch_size=policy.cleanup_batch_size,
        scanner=scanner,
    )


async def _dispatch_upload_validations(
    repository: AudioUploadRepository,
    scheduler: ValidationScheduler,
    runner: UploadValidationRunner,
    merged_runner: MergedTranscriptionValidationRunner,
    zip_runner: ZipArchiveValidationRunner,
    wakeup: asyncio.Event,
) -> None:
    submitted: set[str] = set()
    while True:
        # New rows wake this dispatcher; the slow scan is restart recovery only.
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=2)
            wakeup.clear()
        except TimeoutError:
            pass
        for row in await asyncio.to_thread(repository.queued_validations, limit=100):
            validation_id = str(row["validation_id"])
            if validation_id in submitted:
                continue
            submitted.add(validation_id)

            async def operation(
                item: dict[str, object] = row, identifier: str = validation_id
            ) -> None:
                try:
                    if item["session_archive_mode"]:
                        selected = zip_runner
                    elif item["session_input_type"] == "merged_transcription":
                        selected = merged_runner
                    else:
                        selected = runner
                    await asyncio.to_thread(selected.run, item)
                finally:
                    submitted.discard(identifier)

            scheduler.submit(str(row["session_public_id"]), operation)


async def _upload_maintenance(
    repository: AudioUploadRepository,
    layout: StorageLayout,
    batch_size: int,
    interval_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(
                maintain_uploads, repository, layout, batch_size=batch_size
            )
        except Exception:
            LOGGER.exception("upload_maintenance_failed")


def _load_upload_hmac_key(root: Path, config: RuntimeConfig) -> bytes:
    if config.upload_hmac_key:
        key = config.upload_hmac_key.get_secret_value().encode()
        if len(key) < 32:
            raise RuntimeError("upload HMAC key is invalid")
        return key
    key_path = root / ".upload-hmac-key"
    try:
        key = _read_upload_hmac_key(key_path)
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        try:
            descriptor = os.open(
                key_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError:
            key = _read_upload_hmac_key(key_path)
        else:
            try:
                view = memoryview(key)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("key write failed")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if os.name != "nt":
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
    if len(key) != 32:
        raise RuntimeError("upload HMAC key is invalid")
    return key


def _read_upload_hmac_key(path: Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size != 32
            or (os.name != "nt" and info.st_nlink != 1)
            or (os.name != "nt" and info.st_mode & 0o077)
        ):
            raise RuntimeError("upload HMAC key is invalid")
        chunks: list[bytes] = []
        while data := os.read(descriptor, 32):
            chunks.append(data)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _canonical_origin(value: str) -> str | None:
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
        host = parsed.hostname.lower()
        port = parsed.port
        default = 80 if parsed.scheme == "http" else 443
        authority = host if port in {None, default} else f"{host}:{port}"
        return f"{parsed.scheme}://{authority}"
    except ValueError:
        return None


def _rate_category(request: Request) -> str:
    path = request.url.path
    if path == "/api/v1/metrics/page-view":
        return "polling"
    if request.method == "PATCH" and path.endswith("/chunks"):
        return "upload_chunk"
    if path.endswith("/events"):
        return "sse_open"
    if request.method == "GET":
        return "polling"
    if path.endswith("/sessions") or path.endswith("/jobs"):
        return "public_creation"
    if request.method in {"POST", "DELETE"}:
        return "expensive_command"
    return "polling"
