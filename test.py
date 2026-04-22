import requests

# Paste your actual access token here
access_token = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2NDEwNjAiLCJqdGkiOiI2OWU4OGM5NzhmZmVlZTQyNzkyNDE3OTIiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc3Njg0ODAyMywiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzc2ODk1MjAwfQ.F4yrrvi2xfTTB5oxVIqsRjBbH9NkG-MA98GYe9ef2hk"

url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"

# Try WITHOUT auth first
r = requests.get(url)
print(r.status_code)

# If still 403, try WITH auth
r2 = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
print(r2.status_code)