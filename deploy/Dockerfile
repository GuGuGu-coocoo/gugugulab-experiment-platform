FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY server server
RUN useradd --uid 10001 --create-home gep
USER gep
ENV PYTHONPATH=/app/server GEP_DATA_DIR=/data
EXPOSE 8000
CMD ["gunicorn", "gep.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--access-logfile", "/dev/null"]
