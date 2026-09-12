# Build context is the repo root, because the image needs ../../common too.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY common /app/common
RUN pip install -e ./common

# Install against a stub package first so the dependency layer survives a code
# change. The real app/ is copied in below and picked up by the editable install.
COPY services/support/pyproject.toml /app/pyproject.toml
RUN mkdir -p /app/app && touch /app/app/__init__.py && pip install -e .

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY services/support/app /app/app

# This service owns no schema, so there is nothing to migrate.
ENV RUN_MIGRATIONS=0

EXPOSE 8004
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
