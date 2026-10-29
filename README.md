# AllSave Web

Media downloader for **TikTok, YouTube and Instagram**. One backend service does all the downloading and also runs the Telegram bot.

```
frontend (Next.js, Vercel) ──HTTP──> backend (one Docker container on a VPS)
                                      ├─ FastAPI API + yt-dlp + ffmpeg (per-job temp dirs, deleted after 15 min)
Telegram users <──── MTProto ───────> └─ Telegram bot (pyrogram, same process, same job system)
                                            └─ MongoDB (users, premium, free-tier limits)
```

| Path | What |
|---|---|
| `backend/` | FastAPI API. yt-dlp is the only extraction engine; strict domain allowlist |
| `frontend/` | Single-page Next.js App Router + Tailwind UI |
| `backend/app/telegram/` | Telegram bot, started by the backend when `BOT_TOKEN` is set |

## API

| Method | Path | Body / result |
|---|---|---|
| POST | `/api/info` | `{url}` → `{platform, title, thumbnail, duration, options:[{id, kind, label, ext, height, watermark, size}]}` |
| POST | `/api/jobs` | `{url, option_id}` → `{job_id}` |
| GET | `/api/jobs/{id}` | `{status: queued\|downloading\|processing\|done\|error, progress, error}` |
| GET | `/api/jobs/{id}/file` | Streams the file; it is deleted ~60 s after being served |
| GET | `/healthz` | `{status: "ok"}` |

Errors are always `{"error": {"code", "message"}}`. Codes include `unsupported_site`, `invalid_url`, `private`,
`removed`, `geo_blocked`, `rate_limited`, `login_required`, `too_long`, `too_large`, `live`, `invalid_option`,
`busy`, `too_many_requests` and `not_found`.

**Options.** `v<height>` is video at that height (best video plus best audio, merged to MP4). `audio` is MP3 at 192 kbps.
`f:<format_id>` is an exact format, used for TikTok, where every option is labelled with or without watermark.
If yt-dlp exposes a watermarked TikTok format, both variants are offered. Otherwise you only get the clean ones.

**Safety.**
- URLs must be https and on an exact host allowlist.
- Hosts must resolve only to public IPs.
- Short links (`vm.tiktok.com`, `vt.tiktok.com`) are followed by the backend itself, and every redirect hop is re-validated.
- yt-dlp is restricted to the TikTok, YouTube and Instagram extractors, so the generic extractor is disabled.
- Each job runs in its own UUID directory, and only that directory is ever read.
- Job records and files are purged after `JOB_TTL` (15 min).

## Local development

### Docker (everything)

```bash
cp .env.example .env
docker compose up --build
```

The frontend runs at http://localhost:3000 and the API at http://localhost:8000/docs.
To also run the bot, copy `backend/.env.example` to `backend/.env` and fill in the Telegram section. The backend starts the bot automatically.

### Without Docker

You need Python 3.11+, Node 20+, ffmpeg on `PATH` and [deno](https://deno.com) on `PATH` (yt-dlp needs deno for YouTube).

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload            # http://localhost:8000
.venv/bin/pytest                                    # URL validation, options, jobs, rate limits
```

```bash
cd frontend && cp .env.example .env.local && npm install && npm run dev
```

## Configuration

### Backend (`backend/.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated CORS origins (your Vercel domain in production) |
| `MAX_DURATION` | `1800` | Seconds |
| `MAX_FILESIZE_MB` | `500` | Larger options are hidden; downloads above it are aborted |
| `MAX_CONCURRENT_JOBS` / `MAX_QUEUED_JOBS` | `3` / `20` | Global download semaphore, and the queue cap (returns 503 `busy` when full) |
| `RATE_LIMIT_INFO` / `RATE_LIMIT_JOBS` | `20/minute` / `10/minute` | Per-IP limits (slowapi) |
| `YTDLP_COOKIES_FILE` | none | Netscape cookies.txt for age-restricted or login-walled videos. It is copied per job, so it is never written concurrently |
| `PROXY_URL` | none | e.g. `socks5://user:pass@host:1080` when YouTube blocks the VPS IP |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP of your reverse proxy, so rate limits see the real client IP |
| `JOB_TTL`, `WORK_DIR` | `900`, system temp | |

### Frontend

`NEXT_PUBLIC_API_URL` is the public backend URL, with no trailing slash.

### Telegram bot (same `backend/.env`)

The bot is off unless `BOT_TOKEN` is set. If it fails to start (bad token, MongoDB unreachable), the error is logged and the web API keeps running.

| Variable | Notes |
|---|---|
| `BOT_TOKEN`, `API_ID`, `API_HASH`, `MONGO_URI` | Required to enable the bot |
| `SUDO` | Admin user IDs, comma-separated |
| `CHID`, `CHANNEL_LINK` | Force-subscribe channel for `/start`. `CHID=0` disables it |
| `TELEGRAM_DATA_DIR` | Where the session file is kept (`/data` in Docker). Mount a volume so broadcasts still reach users after restarts |
| `FREE_DAILY_LIMIT` | Default `2`. Free downloads per rolling 24 h |
| `COOLDOWN_MINUTES` | Default `30`. Wait between free downloads |
| `PREMIUM_DAYS` | Default `30` |

## Deployment

**Everything on one VPS (website, backend, bot, MongoDB, HTTPS):** follow [DEPLOY.md](DEPLOY.md). The sections below describe the alternative split setup (backend on a VPS, frontend on Vercel).

### Backend on a VPS (Docker)

1. On the VPS, run `docker build -t allsave-api ./backend`. The image includes ffmpeg and deno.
2. Start the container:

   ```bash
   docker run -d --name allsave-api --restart unless-stopped --env-file backend/.env -v allsave_data:/data -p 127.0.0.1:8000:8000 allsave-api
   ```

3. Put a TLS reverse proxy in front of it. For example, a Caddyfile with `api.example.com { reverse_proxy 127.0.0.1:8000 }`.
   Then set `FORWARDED_ALLOW_IPS` to the proxy's IP as the container sees it. On the default bridge network that is usually `172.17.0.1`.
   Never use `*` while port 8000 is publicly reachable.
4. Set `ALLOWED_ORIGINS=https://your-app.vercel.app` (and your custom domain).

The rate limiter, the job store and the Telegram bot live in that process, so run exactly **one** backend process (uvicorn's default; no `--workers`). Two copies would both answer every Telegram message.

**Keeping yt-dlp working.** Sites change often, and an outdated yt-dlp is the most common cause of `extract_failed`.
`requirements.txt` sets a minimum version, so a rebuild always pulls the newest release:

```bash
docker build --no-cache -t allsave-api ./backend && docker rm -f allsave-api && docker run ...   # same flags as above
```

For a quick hotfix without rebuilding (lost when the container is recreated), run
`docker exec allsave-api pip install -U "yt-dlp[default]"`, then `docker restart allsave-api`.

If YouTube answers with "Sign in to confirm you're not a bot", set `PROXY_URL` (a residential proxy works best) or provide `YTDLP_COOKIES_FILE`. Use a throwaway account, never your personal one.

### Frontend on Vercel

Import the repo, set **Root Directory** to `frontend`, add `NEXT_PUBLIC_API_URL=https://api.example.com`, and deploy.

### Telegram bot

There is nothing extra to deploy: the bot runs inside the backend container. Put the Telegram variables in `backend/.env` and start the container.
It connects out to Telegram (no webhook, no extra port), so it only needs a host that keeps the process running 24/7.
Serverless platforms that sleep don't qualify. A VPS or an always-on Docker host does.

## Migrating from the old bot

- **Same database.** Users and groups (`main.users`, `main.groups`) and premium subscriptions (`Cluster0.sub1`) are read from the same places, so existing data carries over.
  Premium is now valid while `expiry_date` is in the future, instead of an in-memory scheduler deleting the record, so subscriptions survive restarts.
- **New collections.** Free-tier counters live in `main.limits` and pending link selections in `main.pending` (a TTL index removes them after 1 h).
- **New limit semantics.** The old in-memory counter never reset. The new limit is `FREE_DAILY_LIMIT` per rolling 24 h plus the cooldown.
  A failed download is refunded.
- **Removed.** Per-site scrapers, Facebook/Envato/adult handlers, global variables, fixed filenames, and scanning the working directory.
- **Credentials.** All credentials now come from environment variables.
  The old `config.py` committed the bot token, API hash and MongoDB password to a public repository. **Revoke and rotate all three** (BotFather `/revoke`, my.telegram.org, Atlas user password) before deploying.

For personal use. Respect creators' rights and platform terms.
