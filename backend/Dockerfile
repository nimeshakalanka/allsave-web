FROM python:3.11-slim

# ffmpeg: merging video+audio and MP3 extraction. deno: JS runtime yt-dlp needs for YouTube.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-2.9.6 /deno /usr/local/bin/deno

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

USER app
# /data holds the Telegram session file; mount a volume there.
ENV PYTHONUNBUFFERED=1 WORK_DIR=/tmp/allsave-jobs FORWARDED_ALLOW_IPS=127.0.0.1 TELEGRAM_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# Per-IP rate limits need the real client IP: set FORWARDED_ALLOW_IPS to your reverse proxy address
# (never "*" when the port is publicly reachable, or clients can spoof X-Forwarded-For).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
