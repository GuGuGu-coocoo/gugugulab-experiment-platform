FROM python:3.12.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY server server
RUN useradd --uid 10001 --create-home gep
USER gep
ENV PYTHONPATH=/app/server GEP_DATA_DIR=/data
EXPOSE 8000
CMD ["gunicorn", "gep.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--access-logfile", "/dev/null"]
