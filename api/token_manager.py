"""
api/token_manager.py
────────────────────
Upstox token auto-refresh system for Render deployment.

HOW IT WORKS (zero manual work after first setup):
  1. Bot starts → checks if token in Redis is valid
  2. If valid → use it, done
  3. If expired → bot logs warning, keeps retrying
  4. You open: https://your-render-url.onrender.com/auth
  5. Click "Login with Upstox" → Upstox login page
  6. After login, Upstox redirects to /auth/callback?code=...
  7. Server exchanges code for token → saves to Redis
  8. Bot picks up new token on next cycle (within 60 seconds)
  9. No Render dashboard, no env var changes, no redeploy needed

DAILY ROUTINE:
  - Each morning before 9:15 AM IST, open /auth in browser
  - Takes 10 seconds total
  - Or set up a phone shortcut to the /auth URL
"""
import logging, os, re, sys
import requests
from urllib.parse import urlencode

log = logging.getLogger(__name__)

TOKEN_URL    = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL     = "https://api.upstox.com/v2/login/authorization/dialog"
PROFILE_URL  = "https://api.upstox.com/v2/user/profile"
TOKEN_REDIS_KEY = "upstox_access_token"


def get_api_key()      -> str: return os.getenv("UPSTOX_API_KEY", "").strip()
def get_api_secret()   -> str: return os.getenv("UPSTOX_API_SECRET", "").strip()
def get_redirect_uri() -> str:
    # On Render: https://trading-bot-api.onrender.com/auth/callback
    base = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not base:
        base = os.getenv("FRONTEND_URL", "http://localhost:8000").strip().rstrip("/")
        # If FRONTEND_URL is the Vercel URL, use the Render URL instead
        if "vercel.app" in base:
            base = "http://localhost:8000"
    return f"{base}/auth/callback"


def check_token_valid(token: str) -> bool:
    if not token or len(token) < 20:
        return False
    try:
        resp = requests.get(
            PROFILE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def get_stored_token() -> str | None:
    """Get token from Redis first, then env var."""
    # Try Redis
    try:
        from infra.redis_bus import get_data
        t = get_data(TOKEN_REDIS_KEY)
        if t and len(t) > 20:
            return t
    except Exception:
        pass
    # Fallback to env var
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip() or None


def save_token(token: str) -> None:
    """Save token to Redis (persists across restarts)."""
    try:
        from infra.redis_bus import set_data
        set_data(TOKEN_REDIS_KEY, token)
        log.info("Token saved to Redis")
    except Exception as e:
        log.warning(f"Could not save token to Redis: {e}")
    # Also update env var for current process
    os.environ["UPSTOX_ACCESS_TOKEN"] = token


def get_login_url() -> str:
    params = {
        "response_type": "code",
        "client_id":     get_api_key(),
        "redirect_uri":  get_redirect_uri(),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(auth_code: str) -> str:
    """Exchange auth code for access token."""
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept":       "application/json",
        },
        data={
            "code":          auth_code,
            "client_id":     get_api_key(),
            "client_secret": get_api_secret(),
            "redirect_uri":  get_redirect_uri(),
            "grant_type":    "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: HTTP {resp.status_code} — {resp.text}")
    token = resp.json().get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.json()}")
    return token


def get_valid_token() -> str | None:
    """
    Get a valid token. Called by Broker on every init.
    Returns token if valid, None if expired (triggers /auth flow).
    """
    token = get_stored_token()
    if token and check_token_valid(token):
        return token
    log.warning("Upstox token expired or missing. Open /auth to refresh.")
    return None
