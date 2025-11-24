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
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		postgresql-client \
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
