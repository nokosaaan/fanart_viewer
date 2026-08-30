# Backend Dockerfile (placeholder)
# Build a Django backend image. Replace with real instructions.
FROM mcr.microsoft.com/playwright/python:latest

# The Playwright python base image includes browsers and necessary
# system dependencies. We copy only the backend requirements and app
# sources into /app and install Python deps there.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Set non-interactive frontend and timezone before installing tzdata
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

# Install minimal OS packages needed by the entrypoint and DB client
# so that `pg_isready` (used by `entrypoint.sh`) is available.
# postgresql-client comes from the PGDG repo (not Ubuntu's default repo)
# so its major version matches the postgres:15 server image — pg_dump/psql
# refuse to talk to a newer server than themselves.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends wget gnupg2 ca-certificates lsb-release \
	&& wget -q -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
	&& echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		postgresql-client-15 \
		netcat-openbsd \
		tzdata \
	&& ln -fs /usr/share/zoneinfo/Etc/UTC /etc/localtime \
	&& dpkg-reconfigure -f noninteractive tzdata || true \
	&& rm -rf /var/lib/apt/lists/*

# Copy backend requirements (build context is `./backend` when using docker-compose)
# so `requirements.txt` is located at the root of the build context.
COPY requirements.txt /app/requirements.txt
# The base Playwright image already includes a matching `playwright` package
# and browsers. Avoid reinstalling/upgrading `playwright` from requirements to
# prevent binary mismatch between the Python package and bundled browsers.
RUN grep -iEv '^\s*playwright\s*$' /app/requirements.txt > /app/requirements.nodeps.txt \
	&& pip install --no-cache-dir -r /app/requirements.nodeps.txt

# dghs-imgutils and pyrfc6266, installed WITHOUT letting pip pull in/check
# their own declared dependencies (requirements.txt above already installs
# everything they actually need — see the comments there for why each is
# skipped this way: bchlib has no aarch64/Pi4 wheel and isn't needed by
# anything item.tagger imports; pyrfc6266's declared pyparsing~=3.0.7
# conflicts on paper with the pyparsing>=3.1 this image also needs, despite
# working fine against it in practice).
RUN pip install --no-cache-dir --no-deps dghs-imgutils pyrfc6266

# Optional, heavy 'timm' tagger backend (see item/tagger.py's HAVE_TIMM /
# TIMM_MODEL_REPO) — off by default since torch alone is several hundred MB
# and the Pi4 deployment target may not want it at all. Enable per-deployment
# by setting INSTALL_TIMM_TAGGER=1 as a build arg (see docker-compose's
# `web` service `build.args`, driven by the .env var of the same name).
# Without it, item.tagger.HAVE_TIMM is False and selecting that model in the
# UI returns a clear "not available" error rather than crashing.
ARG INSTALL_TIMM_TAGGER=0
COPY requirements-timm.txt /app/requirements-timm.txt
RUN if [ "$INSTALL_TIMM_TAGGER" = "1" ]; then \
		pip install --no-cache-dir -r /app/requirements-timm.txt; \
	fi

# Ensure Playwright python package and browsers are installed in the image.
# Installing here keeps the Python package and browser binaries in sync.
RUN pip install --no-cache-dir playwright \
	&& python -m playwright install --with-deps || true

# Install Google Chrome stable in the image so Playwright can launch the
# real Chrome binary (improves rendering parity with user's desktop).
RUN apt-get update \
	&& apt-get install -y --no-install-recommends wget gnupg2 ca-certificates \
	&& wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
	&& sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list' \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends google-chrome-stable \
	&& rm -rf /var/lib/apt/lists/* || true

# Copy the project into the image
COPY . /app

# Make the entrypoint executable (if present)
RUN if [ -f /app/entrypoint.sh ]; then chmod +x /app/entrypoint.sh; fi

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
