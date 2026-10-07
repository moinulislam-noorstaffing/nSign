FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY seed ./seed
RUN useradd -r -u 1001 studio && mkdir -p /data && chown -R studio /data /app
USER studio
EXPOSE 8000
# localhost, not 127.0.0.1 in the URL: this container has no TrustedHost
# middleware, but keeping probes IPv4-explicit avoids the ::1 trap that broke
# an earlier deployment when busybox wget refused to fall back.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
