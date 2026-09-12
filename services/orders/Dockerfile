# Build context is the repo root, because the image needs ../../common too.
# The same image runs the API, the celery worker and beat; only CMD differs.
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

COPY services/orders/pyproject.toml /app/pyproject.toml
RUN mkdir -p /app/app && touch /app/app/__init__.py && pip install -e .

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY services/orders/alembic.ini /app/alembic.ini
COPY services/orders/alembic /app/alembic
COPY services/orders/app /app/app

EXPOSE 8001
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
