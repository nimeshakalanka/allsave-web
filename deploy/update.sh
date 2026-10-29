#!/usr/bin/env bash
# Pull the latest code and rebuild. The backend is rebuilt without cache so yt-dlp is always the newest release.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.prod.yml build --pull --no-cache backend
docker compose -f docker-compose.prod.yml up -d --build
docker image prune -f
docker compose -f docker-compose.prod.yml ps
