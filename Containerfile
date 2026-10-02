# Linux amd64 bases, pinned to the registry manifests checked on 2026-10-02.
FROM docker.io/library/node:24-bookworm-slim@sha256:5cbc7caba8c2c0f0bca675d1b61b9f2857e1cf1853c6164ee9dd409501a936e7 AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM docker.io/library/python:3.13-slim-bookworm@sha256:88310c082760d93ac7c74d579e95e53a4ab6ea52dd8901abc61a103daf488ac4
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MAESTRO_DATA_DIR=/data
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r backend/requirements.txt \
    && groupadd --gid 1000 maestro \
    && useradd --uid 1000 --gid 1000 --no-log-init --no-create-home maestro \
    && mkdir /data && chown 1000:1000 /data && chmod 700 /data
COPY backend/*.py ./backend/
COPY --from=frontend /frontend/dist ./frontend/dist
USER 1000:1000
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import json,os,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('MAESTRO_LISTEN_PORT','8765')+'/health',timeout=2))['application']=='maestro'"
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--no-access-log"]
