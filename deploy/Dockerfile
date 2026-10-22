FROM python:3.12.15-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && python -m pip uninstall --yes pip
COPY server server
COPY GEC_SDK/gec_web/sdk.js GEC_SDK/gec_web/sdk.js
COPY LICENSE LICENSE
COPY third_party/godot_sqlite_license.md third_party/godot_sqlite_license.md
RUN useradd --uid 10001 --create-home gep \
    && install -d -o gep -g gep -m 0700 /data
USER gep
ENV PYTHONPATH=/app/server GEP_DATA_DIR=/data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["gunicorn", "gep.docker_wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--access-logfile", "/dev/null"]
ARG GEP_REVISION=development
LABEL org.opencontainers.image.title="GEP" org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform" \
      org.opencontainers.image.revision=$GEP_REVISION
