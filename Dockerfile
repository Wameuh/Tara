FROM node:22.12.0-bookworm-slim AS frontend-build
WORKDIR /build/webinterface/frontend
COPY webinterface/frontend/package.json webinterface/frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY webinterface/frontend ./
RUN npm run lint && npm run test && npm run build && npm run check:bundle \
    && npx --no-install openapi-typescript src/api/openapi.json -o src/api/generated.ts --check

FROM ghcr.io/astral-sh/uv:0.8.0 AS uv

FROM python:3.13.1-slim-bookworm AS python-build
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

FROM python:3.13.1-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tara && useradd --uid 10001 --gid tara --no-create-home --shell /usr/sbin/nologin tara \
    && mkdir -p /app /data/runtime /data/backups /config \
    && chown -R 10001:10001 /app /data
WORKDIR /app
COPY --from=python-build /build/.venv /app/.venv
COPY --from=python-build /build/src /app/src
COPY --from=python-build /build/tiktoken-cache /app/tiktoken-cache
COPY --from=frontend-build /build/webinterface/frontend/dist /app/frontend
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TARA_WEB_FRONTEND_DIST=/app/frontend TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "tara_web.main"]
CMD ["--config", "/config/webinterface.yaml", "--host", "0.0.0.0", "--port", "8000"]
