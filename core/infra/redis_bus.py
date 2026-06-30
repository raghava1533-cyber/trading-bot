import json, os, tempfile

_store: dict = {}
REDIS_ON = False
r = None
STATE_FILE = os.path.join(tempfile.gettempdir(), "trading_bot_state.json")

try:
    import redis as _redis
    _url = os.getenv("REDIS_URL","redis://localhost:6379")
    r = _redis.from_url(_url, decode_responses=True)
    r.ping()
    REDIS_ON = True
except Exception:
    REDIS_ON = False

def set_data(key, value):
    sv = str(value)
    if REDIS_ON: r.set(key, sv)
    else: _store[key] = sv
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE,"r",encoding="utf-8") as f: data = json.load(f)
        data[key] = sv
        with open(STATE_FILE,"w",encoding="utf-8") as f: json.dump(data, f)
    except Exception: pass

def get_data(key):
    if REDIS_ON: return r.get(key)
    return _store.get(key)

def get_all_data():
    if REDIS_ON:
        keys = r.keys("*")
        if keys:
            vals = r.mget(keys)
            return {k:v for k,v in zip(keys,vals) if v is not None}
        return {}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: pass
    return dict(_store)
