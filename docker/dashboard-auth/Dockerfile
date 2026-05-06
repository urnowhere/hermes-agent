FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HERMES_DASHBOARD_AUTH_SMOKE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python -m pip install --upgrade pip \
    && python -m pip install -e '.[web,pty]'

RUN cd web && npm ci && npm run build

EXPOSE 9119
