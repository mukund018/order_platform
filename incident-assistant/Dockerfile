# Build context is the repo root, because the image needs ../common too, and
# ../incidents + ../runbooks are mounted in at runtime (read-only - see docker-compose.yml).
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

COPY incident-assistant/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY incident-assistant/*.py /app/

# No schema, nothing to migrate - same reasoning as support-service.
ENV RUN_MIGRATIONS=0

EXPOSE 8005
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
