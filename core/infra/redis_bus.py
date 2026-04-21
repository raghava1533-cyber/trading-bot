try:
    import redis
    r = redis.Redis(host='localhost', port=6379)
    r.ping()
    REDIS_ON = True
except:
    REDIS_ON = False
    _store = {}

def set_data(key, value):
    if REDIS_ON:
        r.set(key, value)
    else:
        _store[key] = value

def get_data(key):
    if REDIS_ON:
        return r.get(key)
    return _store.get(key)