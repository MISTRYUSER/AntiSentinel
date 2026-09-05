FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 antisentinel \
    && mkdir -p /app/storage \
    && chown antisentinel:antisentinel /app/storage
COPY frontend ./frontend
USER antisentinel
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/runtime/config', timeout=3)"
CMD ["uvicorn", "antisentinel.api.app:app", "--host", "0.0.0.0", "--port", "8765"]
