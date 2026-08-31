FROM node:22.23.2-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS frontend-build
WORKDIR /build/webinterface/frontend
COPY webinterface/frontend/package.json webinterface/frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY webinterface/frontend ./
RUN npm run lint && npm run test && npm run build && npm run check:bundle \
    && npx --no-install openapi-typescript src/api/openapi.json -o src/api/generated.ts --check

FROM ghcr.io/astral-sh/uv:0.8.0@sha256:5778d479c0fd7995fedd44614570f38a9d849256851f2786c451c220d7bd8ccd AS uv

FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS python-build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra deploy --no-install-project
RUN TIKTOKEN_CACHE_DIR=/build/tiktoken-cache /build/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
COPY src ./src
COPY scripts/generate_web_openapi.py scripts/generate_i18n_manifest.py ./scripts/
COPY webinterface/frontend/src/i18n ./webinterface/frontend/src/i18n
COPY webinterface/frontend/src/api/openapi.json ./webinterface/frontend/src/api/openapi.json
RUN PYTHONPATH=/build/src /build/.venv/bin/python scripts/generate_web_openapi.py --check \
    && PYTHONPATH=/build/src /build/.venv/bin/python scripts/generate_i18n_manifest.py --check

FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS runtime
ARG OCI_SOURCE="unknown"
ARG OCI_REVISION="unknown"
ARG OCI_VERSION="dev"
LABEL org.opencontainers.image.source=$OCI_SOURCE \
      org.opencontainers.image.revision=$OCI_REVISION \
      org.opencontainers.image.version=$OCI_VERSION \
      org.opencontainers.image.title="Tara Web"
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tara && useradd --uid 10001 --gid tara --no-create-home --shell /usr/sbin/nologin tara \
    && mkdir -p /app/docker /app/scripts /data/runtime/db /data/backups /config \
    && chown -R 10001:10001 /app /data
WORKDIR /app
COPY --from=python-build /build/.venv /app/.venv
COPY --from=python-build /build/src /app/src
COPY --from=python-build /build/tiktoken-cache /app/tiktoken-cache
COPY --from=frontend-build /build/webinterface/frontend/dist /app/frontend
COPY docker/entrypoint.sh docker/healthcheck.py docker/admin_healthcheck.py /app/docker/
COPY scripts/docker_preflight.py scripts/docker_deploy_check.py scripts/docker-migrate.sh scripts/docker-backup.sh scripts/docker-restore.sh /app/scripts/
RUN mkdir -p /restore \
    && chown 10001:10001 /restore \
    && chmod 0700 /restore \
    && chown -R 0:0 /app \
    && chmod -R a=rX /app \
    && chmod 0555 /app/docker/entrypoint.sh /app/docker/healthcheck.py /app/docker/admin_healthcheck.py /app/scripts/*
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=random HOME=/nonexistent TARA_WEB_FRONTEND_DIST=/app/frontend TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
USER 10001:10001
EXPOSE 8000 8765
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["--config", "/config/webinterface.yaml", "--host", "0.0.0.0", "--port", "8000"]
