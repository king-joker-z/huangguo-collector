# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Huangguo Collector"
LABEL org.opencontainers.image.description="Huangguo AI short-drama collector, media library and Jellyfin/Emby scraper"
LABEL org.opencontainers.image.version="1.7.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    HUANGGUO_DATA_DIR=/data \
    HUANGGUO_DOWNLOAD_ROOT=/downloads \
    PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh \
    && mkdir -p /data /downloads

EXPOSE 8765
VOLUME ["/data", "/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]







