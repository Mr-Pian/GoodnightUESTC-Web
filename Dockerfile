FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    UESTC_NETWORK_MODE=direct \
    HOME=/tmp

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data \
    && chown app:app /data

COPY app.py server.py hosted.py session_packages.py browser_login.py checkin.py core.py network.py progress.py ./
COPY vendor/ ./vendor/
COPY static/ ./static/
COPY LICENSE ./LICENSE

USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).close()"
CMD ["python", "server.py"]
