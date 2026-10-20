FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY server server
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
