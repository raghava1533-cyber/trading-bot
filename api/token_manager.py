import logging, os, requests
from urllib.parse import urlencode

log = logging.getLogger(__name__)

TOKEN_URL       = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL        = "https://api.upstox.com/v2/login/authorization/dialog"
PROFILE_URL     = "https://api.upstox.com/v2/user/profile"
TOKEN_REDIS_KEY = "upstox_access_token"


def get_api_key():      return os.getenv("UPSTOX_API_KEY", "").strip()
def get_api_secret():   return os.getenv("UPSTOX_API_SECRET", "").strip()

def get_redirect_uri():
    base = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not base:
        base = "http://localhost:8000"
    return base + "/auth/callback"


def check_token_valid(token):
    if not token or len(token) < 20:
        return False
    try:
        r = requests.get(PROFILE_URL,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def get_stored_token():
    try:
        from infra.redis_bus import get_data
        t = get_data(TOKEN_REDIS_KEY)
        if t and len(t) > 20:
            return t
    except Exception:
        pass
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip() or None


def save_token(token):
    try:
        from infra.redis_bus import set_data
        set_data(TOKEN_REDIS_KEY, token)
        log.info("Token saved to Redis")
    except Exception as e:
        log.warning("Could not save token to Redis: " + str(e))
    os.environ["UPSTOX_ACCESS_TOKEN"] = token


def get_login_url():
    params = {"response_type": "code", "client_id": get_api_key(),
              "redirect_uri": get_redirect_uri()}
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(auth_code):
    resp = requests.post(TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        data={"code": auth_code, "client_id": get_api_key(),
              "client_secret": get_api_secret(),
              "redirect_uri": get_redirect_uri(),
              "grant_type": "authorization_code"},
        timeout=15)
    if resp.status_code != 200:
        raise RuntimeError("Token exchange failed: HTTP " + str(resp.status_code) + " - " + resp.text)
    token = resp.json().get("access_token", "")
    if not token:
        raise RuntimeError("No access_token in response: " + str(resp.json()))
    return token


def get_valid_token():
    token = get_stored_token()
    if token and check_token_valid(token):
        return token
    log.warning("Upstox token expired or missing. Open /auth to refresh.")
    return None
