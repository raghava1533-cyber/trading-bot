""
broker/auth.py
──────────────
Upstox OAuth2 token exchange helper.

WHEN TO RUN:
  - First time setup
  - Every morning before 9:15 AM (token expires at 3:30 AM daily)
  - When you see "401 Unauthorized" errors in the bot

HOW TO USE:
  python core/broker/auth.py

WHAT IT DOES:
  1. Checks if UPSTOX_ACCESS_TOKEN in .env is still valid
     -> If valid, prints confirmation and exits (nothing to do)
  2. If token missing/expired:
     a. Reads UPSTOX_API_KEY + UPSTOX_API_SECRET from .env
     b. Prints a login URL -> open in browser -> log in with Upstox credentials
     c. Browser redirects to your UPSTOX_REDIRECT_URI with ?code=AbCdEf...
     d. Paste the code value when prompted
     e. Exchanges code for fresh access token via Upstox API
     f. Saves new UPSTOX_ACCESS_TOKEN to core/.env automatically

REQUIRED in core/.env:
  UPSTOX_API_KEY     = (from https://developer.upstox.com -> Your Apps)
  UPSTOX_API_SECRET  = (from same page)
  UPSTOX_REDIRECT_URI= (must match exactly what you set in developer portal)

NOT REQUIRED:
  UPSTOX_AUTH_CODE   = (only needed for --auto mode, see below)

OPTIONAL --auto flag (for scheduled/scripted use):
  Set UPSTOX_AUTH_CODE=<code> in .env first, then:
  python core/broker/auth.py --auto
"""
import argparse, os, re, sys, requests
from urllib.parse import urlencode

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_HERE)
sys.path.insert(0, _CORE)

from dotenv import load_dotenv
load_dotenv(os.path.join(_CORE, ".env"))

TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL  = "https://api.upstox.com/v2/login/authorization/dialog"
ENV_FILE  = os.path.join(_CORE, ".env")


def _check_token_valid(token: str) -> bool:
    """Quick check — call /user/profile to see if token is still alive."""
    if not token or len(token) < 20:
        return False
    try:
        resp = requests.get(
            "https://api.upstox.com/v2/user/profile",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def get_login_url(api_key: str, redirect_uri: str) -> str:
    params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(api_key: str, api_secret: str,
                             redirect_uri: str, auth_code: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        data={"code": auth_code, "client_id": api_key, "client_secret": api_secret,
              "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: HTTP {resp.status_code}\n{resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.json()}")
    return token


def save_token_to_env(token: str) -> None:
    """Write UPSTOX_ACCESS_TOKEN into core/.env."""
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(f"UPSTOX_ACCESS_TOKEN={token}\n")
        return
    content = open(ENV_FILE, "r", encoding="utf-8").read()
    new_line = f"UPSTOX_ACCESS_TOKEN={token}"
    if re.search(r"^UPSTOX_ACCESS_TOKEN=.*$", content, re.MULTILINE):
        content = re.sub(r"^UPSTOX_ACCESS_TOKEN=.*$", new_line, content, re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"Saved UPSTOX_ACCESS_TOKEN to {ENV_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Upstox OAuth2 token exchange")
    parser.add_argument("--auto", action="store_true",
                        help="Use UPSTOX_AUTH_CODE from .env without prompting")
    parser.add_argument("--force", action="store_true",
                        help="Force token refresh even if current token is valid")
    args = parser.parse_args()

    api_key      = os.getenv("UPSTOX_API_KEY", "").strip()
    api_secret   = os.getenv("UPSTOX_API_SECRET", "").strip()
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1").strip()
    auth_code    = os.getenv("UPSTOX_AUTH_CODE", "").strip()
    cur_token    = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

    # ── Check if current token is still valid ─────────────────────────────────
    if not args.force and cur_token:
        print("Checking current access token...")
        if _check_token_valid(cur_token):
            print("Access token is valid. Nothing to do.")
            print("Run with --force to refresh anyway.")
            return
        else:
            print("Access token expired or invalid. Getting a new one...")

    # ── Validate API credentials ──────────────────────────────────────────────
    if not api_key or api_key in ("your_api_key_here", ""):
        print("ERROR: UPSTOX_API_KEY not set in core/.env")
        print("  Get it from: https://developer.upstox.com/ -> Your Apps")
        sys.exit(1)
    if not api_secret or api_secret in ("your_secret_key_here", ""):
        print("ERROR: UPSTOX_API_SECRET not set in core/.env")
        sys.exit(1)

    # ── Get auth code ─────────────────────────────────────────────────────────
    placeholder = ("paste_auth_code_here", "", None)
    if not auth_code or auth_code in placeholder:
        login_url = get_login_url(api_key, redirect_uri)
        print()
        print("=" * 70)
        print("STEP 1: Open this URL in your browser and log in with Upstox:")
        print()
        print(f"  {login_url}")
        print()
        print("STEP 2: After login you will be redirected to:")
        print(f"  {redirect_uri}/?code=AbCdEf123456&...")
        print()
        print("STEP 3: Copy the 'code' value from the URL.")
        print("=" * 70)
        print()
        if args.auto:
            print("ERROR: --auto flag set but UPSTOX_AUTH_CODE not in .env")
            sys.exit(1)
        auth_code = input("Paste auth code here: ").strip()
        if not auth_code:
            print("ERROR: No auth code provided.")
            sys.exit(1)

    # ── Exchange for token ────────────────────────────────────────────────────
    print("Exchanging auth code for access token...")
    try:
        token = exchange_code_for_token(api_key, api_secret, redirect_uri, auth_code)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"Access token obtained! (first 20 chars: {token[:20]}...)")
    save_token_to_env(token)

    # Clear auth code from .env after use
    content = open(ENV_FILE, "r", encoding="utf-8").read()
    content = re.sub(r"^UPSTOX_AUTH_CODE=.*$", "UPSTOX_AUTH_CODE=",
                     content, flags=re.MULTILINE)
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("Auth code cleared from .env (one-time use).")
    print("\nDone! Start the bot with:  python core/main_async.py")


if __name__ == "__main__":
    main()
