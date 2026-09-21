FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

RUN groupadd --system app && useradd --system --gid app --home-dir ${APP_HOME} --shell /usr/sbin/nologin app

COPY pyproject.toml README.md ./
COPY app ./app
COPY artifacts ./artifacts
COPY scripts ./scripts

RUN pip install --upgrade pip && pip install . && \
    chown -R app:app ${APP_HOME}

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
