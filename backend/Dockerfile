# Backend Dockerfile (placeholder)
# Build a Django backend image. Replace with real instructions.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
	gcc libpq-dev curl postgresql-client netcat-openbsd && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers and required OS dependencies
RUN python -m playwright install --with-deps || true

COPY . /app

RUN chmod +x /app/entrypoint.sh || true

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
