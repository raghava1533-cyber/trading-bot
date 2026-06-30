import logging, os, requests
log = logging.getLogger(__name__)

REDIS_KEYS = {
    "api_key":      "upstox_api_key",
    "api_secret":   "upstox_api_secret",
    "access_token": "upstox_access_token",
}

def save_credentials(api_key: str, api_secret: str, access_token: str = "") -> dict:
    """Save credentials to Redis + os.environ."""
    try:
        from infra.redis_bus import set_data
        if api_key:
            set_data(REDIS_KEYS["api_key"], api_key)
            os.environ["UPSTOX_API_KEY"] = api_key
        if api_secret:
            set_data(REDIS_KEYS["api_secret"], api_secret)
            os.environ["UPSTOX_API_SECRET"] = api_secret
        if access_token:
            set_data(REDIS_KEYS["access_token"], access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"] = access_token
        log.info("Credentials saved to Redis")
        return {"ok": True}
    except Exception as e:
        log.error("save_credentials: " + str(e))
        return {"ok": False, "error": str(e)}


def load_credentials() -> dict:
    """Load credentials from Redis, fallback to env vars."""
    result = {}
    try:
        from infra.redis_bus import get_data
        result["api_key"]      = get_data(REDIS_KEYS["api_key"])      or os.getenv("UPSTOX_API_KEY", "")
        result["api_secret"]   = get_data(REDIS_KEYS["api_secret"])   or os.getenv("UPSTOX_API_SECRET", "")
        result["access_token"] = get_data(REDIS_KEYS["access_token"]) or os.getenv("UPSTOX_ACCESS_TOKEN", "")
    except Exception:
        result["api_key"]      = os.getenv("UPSTOX_API_KEY", "")
        result["api_secret"]   = os.getenv("UPSTOX_API_SECRET", "")
        result["access_token"] = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    return result


def check_token(token: str) -> dict:
    """Validate token against Upstox API."""
    if not token or len(token) < 20:
        return {"valid": False, "reason": "Token too short"}
    try:
        r = requests.get(
            "https://api.upstox.com/v2/user/profile",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return {"valid": True, "name": data.get("user_name", ""), "email": data.get("email", "")}
        return {"valid": False, "reason": "HTTP " + str(r.status_code)}
    except Exception as e:
        return {"valid": False, "reason": str(e)}
