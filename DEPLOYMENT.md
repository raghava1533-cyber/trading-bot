# Deployment Guide — 100% FREE Setup
# Render (free) + Vercel (free) + UptimeRobot (free)

## Total Cost: Rs 0 / month

| Service | Plan | Cost | Purpose |
|---------|------|------|---------|
| Render Web Service | Free | Rs 0 | Runs FastAPI + trading bot |
| Render Redis | Free | Rs 0 | State storage (25 MB) |
| Vercel | Hobby | Rs 0 | Next.js dashboard UI |
| UptimeRobot | Free | Rs 0 | Keeps Render awake (pings every 5 min) |

---

## Architecture

```
[Render FREE]                    [Vercel FREE]
  api/server.py (FastAPI)  <-->  frontend/ (Next.js)
  + bot background task          NEXT_PUBLIC_API_URL
  + Redis (free addon)           connects to Render

[UptimeRobot FREE]
  Pings /ping every 5 min
  Prevents Render free tier from sleeping
```

> NOTE: Render free tier sleeps after 15 min of no traffic.
> UptimeRobot pings it every 5 min for FREE — keeps it always awake.

---

## STEP 1 — Push code to GitHub (already done)

Your repo: https://github.com/raghava1533-cyber/trading-bot

---

## STEP 2 — Deploy on Render (FREE)

### 2a. Create account
1. Go to https://render.com
2. Click **Get Started for Free**
3. Sign up with **GitHub** (same account as your repo)

### 2b. Create services via Blueprint
1. Click **New +** (top right)
2. Select **Blueprint**
3. Connect your GitHub repo: `raghava1533-cyber/trading-bot`
4. Render reads `render.yaml` automatically
5. It will show 2 services to create:
   - `trading-bot-api` (Web Service)
   - `trading-bot-redis` (Redis)
6. Click **Apply**
7. Wait 3-5 minutes for build to complete

### 2c. Set secret environment variables
After deploy, go to:
**Dashboard → trading-bot-api → Environment**

Click **Add Environment Variable** for each:

| Key | Value | Type |
|-----|-------|------|
| `UPSTOX_ACCESS_TOKEN` | your_token | Secret |
| `UPSTOX_API_KEY` | your_api_key | Secret |
| `UPSTOX_API_SECRET` | your_secret | Secret |
| `FRONTEND_URL` | https://your-app.vercel.app | Plain (set after Step 3) |

All other settings are already in render.yaml (BOT_ENABLED, DRY_RUN, etc.)

### 2d. Get your Render URL
After deploy, your API URL will be:
`https://trading-bot-api.onrender.com`

Test it:
- Open browser: `https://trading-bot-api.onrender.com/health`
- Should show: `{"status":"ok"}`

---

## STEP 3 — Deploy on Vercel (FREE)

### 3a. Create account
1. Go to https://vercel.com
2. Click **Sign Up**
3. Sign up with **GitHub**

### 3b. Import project
1. Click **Add New** → **Project**
2. Find `trading-bot` repo → click **Import**
3. **CRITICAL STEP:** Set Root Directory
   - Look for "Root Directory" field
   - Click the pencil/edit icon
   - Type: `frontend`
   - Click checkmark to confirm
4. Framework Preset: **Next.js** (auto-detected)
5. Do NOT change Build Command or Output Directory

### 3c. Set environment variable
Still on the deploy screen, expand **Environment Variables**:

| Name | Value |
|------|-------|
| `NEXT_PUBLIC_API_URL` | `https://trading-bot-api.onrender.com` |

Click **Add**

### 3d. Deploy
1. Click **Deploy**
2. Wait ~2 minutes
3. Your dashboard URL: something like `https://trading-bot-abc123.vercel.app`
4. Copy this URL — you need it for the next step

### 3e. Update CORS in Render
1. Go back to Render → trading-bot-api → Environment
2. Find `FRONTEND_URL`
3. Change value to your actual Vercel URL
   Example: `https://trading-bot-abc123.vercel.app`
4. Click **Save Changes**
5. Render will auto-redeploy (takes ~1 min)

---

## STEP 4 — Set up UptimeRobot (FREE — keeps Render awake)

Render free tier sleeps after 15 min of no traffic.
UptimeRobot pings your server every 5 min to keep it awake.

### 4a. Create account
1. Go to https://uptimerobot.com
2. Click **Register for FREE**
3. Verify your email

### 4b. Add monitor
1. Click **+ Add New Monitor**
2. Fill in:
   - Monitor Type: **HTTP(s)**
   - Friendly Name: `Trading Bot API`
   - URL: `https://trading-bot-api.onrender.com/ping`
   - Monitoring Interval: **5 minutes**
3. Click **Create Monitor**

That's it! UptimeRobot will now ping your server every 5 minutes for free.
Your Render service will NEVER sleep during market hours.

---

## STEP 5 — Verify everything works

### Check Render API:
Open these URLs in your browser:

```
https://trading-bot-api.onrender.com/health
→ {"status":"ok"}

https://trading-bot-api.onrender.com/ping
→ {"pong":true,"time":"2026-06-30T..."}

https://trading-bot-api.onrender.com/state
→ {"indices":{"NIFTY":{"pnl":null,"spot":null,"regime":null}},...}
```

### Check Vercel dashboard:
1. Open your Vercel URL
2. You should see the trading dashboard
3. Status shows "Reconnecting..." (normal — bot not started yet)
4. Once bot is running on Render, it shows "Live" with green dot

### Check Render logs:
Dashboard → trading-bot-api → Logs
Look for:
```
Auth OK - logged in as: Your Name
Model ready
Bot running.
```

---

## STEP 6 — Daily Upstox token refresh

Upstox access tokens expire every day at midnight.
You must refresh the token each morning before market opens.

### Option A: Manual (simplest)
Each morning before 9:15 AM IST:
1. Run locally: `python core/broker/auth.py`
2. Copy the new token
3. Go to Render → trading-bot-api → Environment
4. Update `UPSTOX_ACCESS_TOKEN`
5. Click Save → Render redeploys in ~1 min

### Option B: GitHub Actions (automated)
Create `.github/workflows/refresh_token.yml`:
```yaml
name: Refresh Upstox Token
on:
  schedule:
    - cron: '30 3 * * 1-5'  # 9:00 AM IST Mon-Fri (3:30 UTC)
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: {python-version: '3.11'}
      - run: pip install requests python-dotenv
      - run: python core/broker/auth_refresh.py
        env:
          UPSTOX_API_KEY: ${{ secrets.UPSTOX_API_KEY }}
          UPSTOX_API_SECRET: ${{ secrets.UPSTOX_API_SECRET }}
          RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}
          RENDER_SERVICE_ID: ${{ secrets.RENDER_SERVICE_ID }}
```

---

## STEP 7 — Local development

Run bot + dashboard locally (no Render/Vercel needed):

```bash
# Terminal 1: Start the bot (auto-launches Streamlit dashboard)
cd "C:\Users\rerugant\Downloads\trading bot\trading-bot\core"
python main_async.py

# Streamlit dashboard: http://localhost:8501
# Bot logs: trading_bot.log

# Terminal 2: Start Next.js frontend (optional)
cd "C:\Users\rerugant\Downloads\trading bot\trading-bot\frontend"
npm install
npm run dev
# Frontend: http://localhost:3000
# (needs NEXT_PUBLIC_API_URL=http://localhost:8000 in frontend/.env.local)

# Terminal 3: Start FastAPI server only (optional)
cd "C:\Users\rerugant\Downloads\trading bot\trading-bot"
uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
# API: http://localhost:8000
```

---

## STEP 8 — Troubleshooting

### "Bot not starting" on Render
Check Render logs for error. Common causes:
- `UPSTOX_ACCESS_TOKEN` not set → add it in Environment tab
- `PYTHONPATH` not set → should be `./core` (already in render.yaml)
- Build failed → check build logs for missing packages

### "Cannot connect to backend" on Vercel
- Check `NEXT_PUBLIC_API_URL` has no trailing slash
- Check `FRONTEND_URL` in Render matches your Vercel URL exactly
- Wait 30 seconds after Render redeploy for CORS to update

### "Redis connection failed"
- Redis addon auto-connects via `REDIS_URL` env var
- Check Redis service shows "Available" in Render dashboard
- If Redis is down, bot falls back to STATE_FILE (still works)

### Dashboard shows "Reconnecting..."
- Normal if bot hasn't started yet
- Check Render logs for bot startup messages
- Free tier may take 30-60 seconds to wake up on first request

### Render sleeping despite UptimeRobot
- Check UptimeRobot monitor is active (green)
- Verify URL is exactly: `https://trading-bot-api.onrender.com/ping`
- Check UptimeRobot logs — should show 200 OK every 5 min

### "Token invalid (HTTP 401)" in Render logs
- Upstox token expired (happens daily at midnight)
- Update `UPSTOX_ACCESS_TOKEN` in Render Environment tab
- Token format: long string starting with `eyJ...`

---

## Quick Reference

```
Render API URL:    https://trading-bot-api.onrender.com
Vercel UI URL:     https://your-app.vercel.app
UptimeRobot ping:  https://trading-bot-api.onrender.com/ping

Local bot:         python core/main_async.py
Local dashboard:   http://localhost:8501
Local API:         http://localhost:8000
Local frontend:    http://localhost:3000

Render dashboard:  https://dashboard.render.com
Vercel dashboard:  https://vercel.com/dashboard
UptimeRobot:       https://uptimerobot.com/dashboard
```

---

## Summary of all steps

```
1. GitHub repo already pushed ✓
2. Render:
   a. render.com → New → Blueprint → connect repo
   b. Set UPSTOX_ACCESS_TOKEN, UPSTOX_API_KEY, UPSTOX_API_SECRET as secrets
   c. Copy your Render URL: https://trading-bot-api.onrender.com
3. Vercel:
   a. vercel.com → New Project → import repo
   b. Root Directory = frontend
   c. NEXT_PUBLIC_API_URL = https://trading-bot-api.onrender.com
   d. Deploy → copy your Vercel URL
4. Back in Render: set FRONTEND_URL = your Vercel URL → Save
5. UptimeRobot:
   a. uptimerobot.com → Add Monitor → HTTP(s)
   b. URL = https://trading-bot-api.onrender.com/ping
   c. Interval = 5 minutes
6. Done! Total cost: Rs 0
```
