"""
broker/auth.py
──────────────
Upstox OAuth2 token exchange helper.

HOW TO USE (run this every morning before starting the bot):
─────────────────────────────────────────────────────────────
  python core/broker/auth.py

STEP 1 — First time setup:
  Set in core/.env:
    UPSTOX_API_KEY     = (from https://developer.upstox.com/ -> Your Apps)
    UPSTOX_API_SECRET  = (from same page)
    UPSTOX_REDIRECT_URI= https://127.0.0.1   (must match portal setting)

STEP 2 — Every trading day (token expires at 3:30 AM next day):
  Run:  python core/broker/auth.py
  It prints a login URL like:
    https://api.upstox.com/v2/login/authorization/dialog?...
  Open that URL in your browser.
  Log in with your Upstox credentials.
  You will be redirected to something like:
    https://127.0.0.1/?code=AbCdEf123456&...
  Copy the value of the 'code' parameter (e.g. AbCdEf123456).
  Paste it when prompted (or set UPSTOX_AUTH_CODE in .env first).

STEP 3 — The script exchanges the code for an access token and:
  - Prints the access token
  - Saves it to core/.env as UPSTOX_ACCESS_TOKEN automatically
  - The bot reads UPSTOX_ACCESS_TOKEN on startup

AUTOMATION (optional):
  Set UPSTOX_AUTH_CODE in .env before running to skip the prompt:
    UPSTOX_AUTH_CODE=AbCdEf123456
  Then run:  python core/broker/auth.py --auto
"""
import argparse
import os
import re
import sys
import requests
from urllib.parse import urlencode, urlparse, parse_qs

# ── make sure core/ is on path ────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_HERE)
sys.path.insert(0, _CORE)

from dotenv import load_dotenv
load_dotenv(os.path.join(_CORE, ".env"))

TOKEN_URL   = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL    = "https://api.upstox.com/v2/login/authorization/dialog"
ENV_FILE    = os.path.join(_CORE, ".env")


def get_login_url(api_key: str, redirect_uri: str) -> str:
    params = {
        "response_type": "code",
        "client_id":     api_key,
        "redirect_uri":  redirect_uri,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(api_key: str, api_secret: str,
                             redirect_uri: str, auth_code: str) -> str:
    """Exchange auth code for access token. Returns the token string."""
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept":       "application/json"},
        data={
            "code":          auth_code,
            "client_id":     api_key,
            "client_secret": api_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed: HTTP {resp.status_code}\n{resp.text}"
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def save_token_to_env(token: str) -> None:
    """Write UPSTOX_ACCESS_TOKEN=<token> into core/.env."""
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(f"UPSTOX_ACCESS_TOKEN={token}\n")
        print(f"Created {ENV_FILE} with access token.")
        return

    content = open(ENV_FILE, "r", encoding="utf-8").read()

    # Replace existing line or append
    pattern = r"^UPSTOX_ACCESS_TOKEN=.*$"
    new_line = f"UPSTOX_ACCESS_TOKEN={token}"
    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"

    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"Saved UPSTOX_ACCESS_TOKEN to {ENV_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Upstox OAuth2 token exchange")
    parser.add_argument("--auto", action="store_true",
                        help="Use UPSTOX_AUTH_CODE from .env without prompting")
    args = parser.parse_args()

    api_key      = os.getenv("UPSTOX_API_KEY", "").strip()
    api_secret   = os.getenv("UPSTOX_API_SECRET", "").strip()
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1").strip()
    auth_code    = os.getenv("UPSTOX_AUTH_CODE", "").strip()

    # ── Validate credentials ──────────────────────────────────────────────────
    if not api_key or api_key == "your_api_key_here":
        print("ERROR: UPSTOX_API_KEY not set in core/.env")
        print("  Get it from: https://developer.upstox.com/ -> Your Apps")
        sys.exit(1)
    if not api_secret or api_secret == "your_secret_key_here":
        print("ERROR: UPSTOX_API_SECRET not set in core/.env")
        sys.exit(1)

    # ── Get auth code ─────────────────────────────────────────────────────────
    if not auth_code or auth_code == "paste_auth_code_here":
        login_url = get_login_url(api_key, redirect_uri)
        print()
        print("=" * 70)
        print("STEP 1: Open this URL in your browser and log in:")
        print()
        print(f"  {login_url}")
        print()
        print("STEP 2: After login you will be redirected to something like:")
        print(f"  {redirect_uri}/?code=AbCdEf123456&...")
        print()
        print("STEP 3: Copy the 'code' value from the URL and paste below.")
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
    print(f"\nExchanging auth code for access token...")
    try:
        token = exchange_code_for_token(api_key, api_secret, redirect_uri, auth_code)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"\nAccess token obtained successfully!")
    print(f"Token (first 20 chars): {token[:20]}...")

    # ── Save to .env ──────────────────────────────────────────────────────────
    save_token_to_env(token)

    # ── Clear auth code from .env (one-time use) ──────────────────────────────
    content = open(ENV_FILE, "r", encoding="utf-8").read()
    content = re.sub(r"^UPSTOX_AUTH_CODE=.*$", "UPSTOX_AUTH_CODE=",
                     content, flags=re.MULTILINE)
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("Auth code cleared from .env (one-time use).")
    print("\nDone! You can now start the bot:")
    print("  python core/main_async.py")


if __name__ == "__main__":
    main()
