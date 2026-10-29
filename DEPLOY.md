# Deploy everything on one Ubuntu VPS

Caddy (automatic HTTPS) → Next.js website + FastAPI backend (with the Telegram bot) + MongoDB, all in Docker.
Only ports 80/443 are exposed. Tested layout: Ubuntu 22.04/24.04, x86 or ARM (Oracle Ampere), 1 GB+ RAM.

## 0. Before you start (on your PC)

- Rotate the old leaked credentials: new bot token (@BotFather → `/revoke`), new API ID/hash (my.telegram.org), new Atlas password if you keep Atlas.
- Pick an address for the site:
  - **Your own domain:** add a DNS **A record**, e.g. `allsave.example.com` → your VPS IP.
  - **No domain:** use the free `sslip.io` name for your IP, e.g. IP `140.238.1.2` → `140-238-1-2.sslip.io` (no setup needed).
- Push the code to a **private** GitHub repo (in the `ALL SAVE WEB` folder, PowerShell):

```bash
git init -b main
git add .
git commit -m "AllSave"
git remote add origin https://github.com/<you>/allsave-web.git
git push -u origin main
```

(Create the empty private repo on github.com first. `.env`, `.venv` and `node_modules` are git-ignored.)

## 1. Connect and install Docker (on the VPS)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Reconnect, then check: `docker run --rm hello-world`.

Low-RAM VPS (1–2 GB) only: add swap so builds don't run out of memory.

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 2. Open ports 80 and 443

- **Provider firewall:** Oracle: Networking → VCN → Security List → add Ingress rules TCP 80, TCP 443 (source `0.0.0.0/0`). Other providers: their firewall panel.
- **Oracle Ubuntu images also block ports inside the VM:**

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p udp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

- Other providers using `ufw`: `sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable`.

## 3. Get the code

Private repo: give the VPS a read-only deploy key.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Copy the printed key into GitHub → your repo → Settings → Deploy keys → Add (read-only). Then:

```bash
git clone git@github.com:<you>/allsave-web.git ~/allsave
cd ~/allsave
```

(No GitHub? Drag the project folder into `/home/ubuntu/allsave` with MobaXterm's SFTP panel, skipping `.venv`, `node_modules` and `.next`. You then update by re-uploading instead of `git pull`.)

## 4. Configure

```bash
cd ~/allsave
echo "DOMAIN=allsave.example.com" > .env
cp backend/.env.example backend/.env
nano backend/.env
```

In `backend/.env` set at least:

- `BOT_TOKEN`, `API_ID`, `API_HASH`, `SUDO` (your Telegram user id), `CHID` (0 = no forced channel).
- `MONGO_URI=mongodb://mongo:27017` uses the bundled database. To keep your old bot's users and premium list, use your Atlas URI instead.
- Optional: `MAX_FILESIZE_MB`, `MAX_DURATION`, `PROXY_URL`, `YTDLP_COOKIES_FILE`.

Save with Ctrl+O, Enter, then exit with Ctrl+X.

## 5. Start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes a few minutes (longer on ARM). Then check:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
curl https://allsave.example.com/healthz
```

The backend log should show `Telegram bot @YourBot is running`. Open `https://allsave.example.com` in your browser and message the bot.

## 6. Update and maintain

```bash
cd ~/allsave && bash deploy/update.sh
```

This pulls the latest code and rebuilds; the backend is rebuilt from scratch so yt-dlp is always current. To do that automatically every Monday at 04:00 (`crontab -e`, then add):

```
0 4 * * 1 cd $HOME/allsave && bash deploy/update.sh >> $HOME/allsave-update.log 2>&1
```

Useful commands (run in `~/allsave`):

| What | Command |
|---|---|
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Logs | `docker compose -f docker-compose.prod.yml logs -f backend` (or `caddy`, `frontend`) |
| Restart after editing `backend/.env` | `docker compose -f docker-compose.prod.yml up -d backend` |
| Stop everything | `docker compose -f docker-compose.prod.yml down` |
| Back up MongoDB | `docker compose -f docker-compose.prod.yml exec -T mongo mongodump --archive > mongo-$(date +%F).archive` |

## Troubleshooting

| Problem | Fix |
|---|---|
| Site doesn't load / certificate errors in `caddy` logs | DNS not pointing at the VPS yet, or ports 80/443 closed (step 2). Caddy retries automatically |
| `Telegram bot failed to start` | Read the reason under it: wrong `BOT_TOKEN`/`API_ID`/`API_HASH`, or `MONGO_URI` unreachable |
| Bot answers twice | The same token is running somewhere else (old bot, your PC). Stop the other copy |
| YouTube: "Sign in to confirm you're not a bot" | Datacenter IP blocked. Add `YTDLP_COOKIES_FILE` (throwaway account) or `PROXY_URL` |
| Everything says `extract_failed` | yt-dlp is outdated: `bash deploy/update.sh` |
