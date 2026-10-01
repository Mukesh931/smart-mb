# Deploying Smart-MB

Two pieces of infrastructure: **GitHub** (source of truth) and **Render** (hosting the
FastAPI app + SQLite data). Everything is already configured in the repo — `render.yaml`,
`Procfile`, `runtime.txt` and a `SMARTMB_DATA_DIR` env var that points the database at a
persistent disk.

---

## 0 · Prerequisites

| Item | Notes |
|---|---|
| GitHub account | free |
| Render account | free tier works for a demo; a **paid instance (`starter`)** is required for the persistent disk that keeps the Master CSR DB, uploads and photos across deploys |
| Python 3.13 locally (optional) | only to run the app on your machine |

---

## 1 · Push to GitHub

```bash
cd smart-mb
git init -b main
git add .
git commit -m "Smart-MB: Centralized CSR Database & Site Verification System"
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

Replace `<your-user>/<your-repo>`. `.gitignore` already excludes `data/` (the runtime SQLite
file, uploaded estimates, site photos), `__pycache__/`, virtualenvs and editor files — so no
live rate data or site evidence ends up in the repo.

---

## 2 · Deploy on Render

### Option A — Blueprint (recommended, uses `render.yaml`)

1. Sign in at <https://dashboard.render.com> → **New +** → **Blueprint**.
2. Select the GitHub repository you just pushed (grant access if prompted).
3. Render reads `render.yaml` and shows the **smart-mb** web service. Click **Apply**.
4. Wait for the build (`pip install -r requirements.txt`) and the first boot. On the very
   first start the app seeds itself: 2,268 Master CSR rows (2 FY × 7 regions), the demo
   users and one fully measured demo project.
5. Open the service URL — you should land on the Smart-MB login screen.

### Option B — Manual web service (if you prefer the dashboard)

| Setting | Value |
|---|---|
| Environment | `Python 3` |
| Region | Singapore (closest Render region to Maharashtra) |
| Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
| Start Command | `python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level warning` |
| Health Check Path | `/api/health` |
| Env var `PYTHON_VERSION` | `3.13.0` |
| Env var `SMARTMB_SECRET` | any long random string (signs session tokens) |
| Env var `SMARTMB_DATA_DIR` | `/var/data` |
| Disk | name `smart-mb-data`, mount path `/var/data`, 1 GB |

---

## 3 · Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SMARTMB_SECRET` | **yes in production** | HMAC key for session tokens. If unset the app falls back to a development default — always set this on Render. Rotating it signs everyone out. |
| `SMARTMB_DATA_DIR` | recommended | Directory for `smartmb.sqlite3`, `uploads/` and `photos/`. Point it at the mounted disk (`/var/data`) so data survives restarts and deploys. Defaults to `./data`. |
| `SMARTMB_DB_PATH` | optional | Full path to the SQLite file if you want it outside `SMARTMB_DATA_DIR`. |
| `PYTHON_VERSION` | optional | `3.13.0` (also pinned in `runtime.txt`). |
| `PORT` | injected by Render | The app binds `0.0.0.0:$PORT` via the start command. |

> **Free tier caveat.** Render's free instance has no persistent disk: the SQLite file lives on
> the ephemeral filesystem, so measurements, uploads and photos are lost on every deploy or
> restart (the app simply re-seeds the master data and demo project on boot). For real field
> use either move to a paid instance with the 1 GB disk in `render.yaml`, or switch
> `app/db.py` to PostgreSQL (Render Postgres) — the schema maps 1:1.

---

## 4 · Post-deploy checklist

```bash
BASE=https://<your-service>.onrender.com

curl -s $BASE/api/health                    # {"ok": true, "items": 2268, ...}
curl -s -o /dev/null -w '%{http_code}\n' $BASE/       # 200 -> the SPA is served
```

Then in the browser:

1. Sign in as **Super Admin** — `admin@pwd.maharashtra.gov.in` / `Admin@123` — and **change
   the password immediately** (Admin ▸ Users ▸ Reset password). Do the same for the three
   demo engineer accounts, or delete them and create real ones.
2. Open **Master CSR** ▸ *Master CSR control* and import the official FY file for each region
   (`Item No | Description | Unit | Material Rate | Labour Rate | Completed Rate | ...`).
   Use *Download template* to get the expected columns.
3. Create a project, upload a Technical Sanction estimate, and walk the checklist →
   measurement → Form-23 flow once end-to-end before circulating the URL.

---

## 5 · Day-2 operations

| Task | How |
|---|---|
| New rates (annual CSR revision) | Admin ▸ Master CSR ▸ upload → *Preview & validate* → *Commit* |
| Back up | Render disk snapshot, or `sqlite3 /var/data/smartmb.sqlite3 ".backup /tmp/backup.sqlite3"` from the shell |
| Watch errors | Render ▸ Logs (uvicorn at `--log-level warning`) |
| Health / uptime | `GET /api/health` returns the master-row and project counts |
| Scale | add `--workers 2` to the start command; SQLite in WAL mode handles it, but PostgreSQL is the real answer |

---

## 6 · Alternative: Docker or any VM

```bash
docker run -p 8000:8000 -e SMARTMB_SECRET=change-me \
  -e SMARTMB_DATA_DIR=/data -v smartmb-data:/data smart-mb
```

On a plain VM, `./run.sh` (or systemd + `uvicorn`) with Nginx as a TLS-terminating reverse
proxy is enough; only `SMARTMB_SECRET` and `SMARTMB_DATA_DIR` need to be set.
