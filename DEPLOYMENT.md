# Vercel + Render Deployment Guide

## Architecture

```
[Your Local Machine]          [Render.com]              [Vercel.com]
  python main_async.py  -->   FastAPI server        -->  Next.js dashboard
  (bot + Streamlit)           api/server.py              frontend/
                              + Redis addon              NEXT_PUBLIC_API_URL
                              + bot background task      connects to Render
```

---

## PART 1 — RENDER (Backend + Bot)

### Step 1: Create Render account
1. Go to https://render.com → Sign up with GitHub
2. Click **New** → **Blueprint**
3. Connect your GitHub repo: `raghava1533-cyber/trading-bot`
4. Render reads `render.yaml` automatically — it creates:
   - `trading-bot-api` (Web Service, Python)
   - `trading-bot-redis` (Redis addon)

### Step 2: Set environment variables in Render
Go to **trading-bot-api** → **Environment** tab → Add these:

| Key | Value | Notes |
|-----|-------|-------|
| `UPSTOX_ACCESS_TOKEN` | `your_token_here` | Mark as **Secret** |
| `UPSTOX_API_KEY` | `your_api_key` | From Upstox developer portal |
| `UPSTOX_API_SECRET` | `your_secret` | From Upstox developer portal |
| `FRONTEND_URL` | `https://your-app.vercel.app` | Set AFTER Vercel deploy |
| `BOT_ENABLED` | `true` | Set `false` to run API only |
| `DRY_RUN` | `true` | Set `false` for live trading |
| `ACTIVE_INDICES` | `NIFTY` | Or `NIFTY,BANKNIFTY` |
| `STOP_LOSS` | `-1500` | Auto-close loss threshold |
| `TARGET_PROFIT` | `1000` | Auto-close profit threshold |
| `TARGET_DELTA` | `0.30` | Strike selection delta |
| `SPREAD_WIDTH_POINTS` | `200` | Spread width in points |
| `MIN_CREDIT_RATIO` | `0.25` | Min credit quality filter |
| `PYTHONPATH` | `./core` | Already in render.yaml |

### Step 3: Deploy
1. Click **Apply** in the Blueprint screen
2. Wait ~3 minutes for build
3. Check logs: **trading-bot-api** → **Logs**
4. Test: visit `https://trading-bot-api.onrender.com/health`
   - Should return: `{"status":"ok"}`
5. Test state: `https://trading-bot-api.onrender.com/state`

### Step 4: Upstox token refresh (daily)
Upstox tokens expire daily. Options:
- **Option A (recommended):** Set up a cron job or GitHub Action to refresh token
- **Option B:** Manually update `UPSTOX_ACCESS_TOKEN` in Render env vars each morning
- **Option C:** Use Upstox's long-lived token if available

---

## PART 2 — VERCEL (Frontend Dashboard)

### Step 1: Create Vercel account
1. Go to https://vercel.com → Sign up with GitHub

### Step 2: Import project
1. Click **Add New** → **Project**
2. Import `raghava1533-cyber/trading-bot`
3. **IMPORTANT:** Set **Root Directory** to `frontend`
   - Click "Edit" next to Root Directory
   - Type: `frontend`
   - Click Save
4. Framework: **Next.js** (auto-detected)

### Step 3: Set environment variables in Vercel
Go to **Settings** → **Environment Variables**:

| Key | Value |
|-----|-------|
| `NEXT_PUBLIC_API_URL` | `https://trading-bot-api.onrender.com` |

### Step 4: Deploy
1. Click **Deploy**
2. Wait ~2 minutes
3. Your dashboard URL: `https://trading-bot-dashboard.vercel.app` (or similar)

### Step 5: Update CORS in Render
1. Go back to Render → **trading-bot-api** → **Environment**
2. Update `FRONTEND_URL` = your actual Vercel URL (e.g. `https://trading-bot-dashboard.vercel.app`)
3. Click **Save Changes** → Render auto-redeploys

---

## PART 3 — LOCAL STREAMLIT DASHBOARD

The Streamlit dashboard still works locally alongside the bot:

```bash
# Terminal 1: Run the bot (also auto-launches Streamlit)
cd "C:\Users\rerugant\Downloads\trading bot\trading-bot\core"
python main_async.py

# Dashboard opens at: http://localhost:8501

# Terminal 2: Or run Streamlit separately
cd "C:\Users\rerugant\Downloads\trading bot\trading-bot\core"
streamlit run dashboard/app.py
```

---

## PART 4 — VERIFY EVERYTHING WORKS

### Check Render backend:
```
GET https://trading-bot-api.onrender.com/health
→ {"status":"ok"}

GET https://trading-bot-api.onrender.com/state
→ {"indices":{"NIFTY":{"pnl":...,"spot":...,"regime":...}}, ...}

GET https://trading-bot-api.onrender.com/history
→ {"trades":[...]}
```

### Check Vercel frontend:
- Open your Vercel URL
- Should show "Live" green dot (WebSocket connected)
- Should show NIFTY spot price updating every second

### Close a position via API:
```bash
curl -X POST https://trading-bot-api.onrender.com/close \
  -H "Content-Type: application/json" \
  -d '{"index":"NIFTY","action":"close_all"}'
```

---

## PART 5 — TROUBLESHOOTING

### Render: "Bot not starting"
- Check logs for `Auth OK` message
- Verify `UPSTOX_ACCESS_TOKEN` is set and valid
- Check `PYTHONPATH=./core` is set

### Render: "Redis connection failed"
- Redis addon auto-connects via `REDIS_URL` env var
- Check Redis service is running in Render dashboard

### Vercel: "Cannot connect to backend"
- Check `NEXT_PUBLIC_API_URL` is set correctly (no trailing slash)
- Check CORS: `FRONTEND_URL` in Render must match your Vercel URL exactly
- Check Render service is not sleeping (free tier sleeps after 15min)

### Render free tier sleeping:
- Free tier sleeps after 15 min of inactivity
- Upgrade to **Starter** plan ($7/mo) for always-on
- Or use UptimeRobot to ping `/health` every 5 min

### Upstox token expired:
- Render logs will show: `Token invalid (HTTP 401)`
- Update `UPSTOX_ACCESS_TOKEN` in Render env vars
- Or run `python core/broker/auth.py` locally and copy the new token

---

## PART 6 — COSTS

| Service | Plan | Cost | Notes |
|---------|------|------|-------|
| Render Web Service | Starter | $7/mo | Always-on, needed for bot |
| Render Redis | Free | $0 | 25MB, sufficient |
| Vercel | Hobby | $0 | Free for personal projects |
| **Total** | | **$7/mo** | |

Free tier works but Render sleeps after 15min inactivity.

---

## PART 7 — QUICK REFERENCE

```
Render API:    https://trading-bot-api.onrender.com
Vercel UI:     https://your-app.vercel.app
Local bot:     python core/main_async.py
Local dash:    http://localhost:8501
State file:    %TEMP%\trading_bot_state.json
Trade history: %TEMP%\trade_history.json
```
