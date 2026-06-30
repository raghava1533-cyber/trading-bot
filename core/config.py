"""Application configuration — all values from .env with safe defaults."""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from datetime import time
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

def _get_int(name, default):
    v = os.getenv(name)
    return default if (v is None or v == "") else int(v)

def _get_float(name, default):
    v = os.getenv(name)
    return default if (v is None or v == "") else float(v)

def _get_bool(name, default):
    v = os.getenv(name)
    return default if (v is None or v == "") else v.strip().lower() in {"1","true","yes","y","on"}

def _get_time(name, default):
    raw = os.getenv(name, default)
    h, m = raw.split(":", 1)
    return time(int(h), int(m))

def _load_index_config():
    default = {
        "NIFTY":     {"lot_size":75,  "yf_ticker":"^NSEI",    "label":"NIFTY 50",   "range_size":1000, "exchange":"NSE_FO","instrument_type":"OPTIDX","index_key":"NSE_INDEX|Nifty 50"},
        "BANKNIFTY": {"lot_size":30,  "yf_ticker":"^NSEBANK", "label":"BANK NIFTY", "range_size":2000, "exchange":"NSE_FO","instrument_type":"OPTIDX","index_key":"NSE_INDEX|Nifty Bank"},
        "SENSEX":    {"lot_size":10,  "yf_ticker":"^BSESN",   "label":"SENSEX",     "range_size":2000, "exchange":"BSE_FO","instrument_type":"OPTIDX","index_key":"BSE_INDEX|SENSEX"},
    }
    raw = os.getenv("INDEX_CONFIG_JSON")
    if not raw: return default
    loaded = json.loads(raw)
    return loaded if (isinstance(loaded, dict) and loaded) else default

@dataclass(frozen=True)
class Settings:
    active_indices: tuple
    poll_interval_seconds: int; trade_cooldown_seconds: int; max_trades_per_day: int
    stop_loss: float; target_profit: float; target_delta: float
    min_credit_ratio: float          # NEW: min credit/spread ratio (e.g. 0.25 = 25%)
    delta_hedge_threshold: float; risk_free_rate: float
    fallback_time_to_expiry_years: float; min_time_to_expiry_days: float
    spread_width_points: int; price_scan_range: float; exposure_margin_rate: float
    default_iv: float; default_oi: int; min_oi: int
    oi_score_window_points: int; oi_score_normalizer: float
    market_open_time: time; market_close_time: time
    log_file: str; redis_url: str; model_path: str
    instrument_cache_file: str; instrument_cache_ttl_hours: float; instrument_master_url: str
    http_timeout_seconds: int; api_retries: int; api_retry_sleep_seconds: float
    quote_throttle_seconds: float; order_product: str; order_validity: str; order_tag: str
    dry_run: bool; regime_side_vol_threshold: float
    backtest_initial_capital: float; backtest_days: int; backtest_interval: str
    backtest_entry_every_bars: int; backtest_holding_bars: int
    backtest_commission_per_order: float; backtest_slippage_bps: float
    backtest_strike_step: int; backtest_chain_width_points: int
    backtest_stop_loss: float; backtest_target_profit: float
    backtest_output_dir: str; backtest_option_data_path: str
    backtest_option_data_format: str; backtest_option_timestamp_tolerance_minutes: int
    backtest_allow_synthetic_fallback: bool

def _apply_redis_overrides():
    """Pull cfg_* keys from Redis into os.environ so load_settings() picks them up."""
    try:
        import redis as _redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r   = _redis.from_url(url, decode_responses=True, socket_timeout=2)
        keys = r.keys("cfg_*")
        for k in keys:
            env_key = k[4:].upper()   # cfg_dry_run -> DRY_RUN
            val     = r.get(k)
            if val is not None:
                os.environ[env_key] = val
    except Exception:
        pass   # Redis unavailable - use existing env vars

_apply_redis_overrides()

def load_settings():
    indices = tuple(i.strip().upper() for i in os.getenv("ACTIVE_INDICES","NIFTY,BANKNIFTY,SENSEX").split(",") if i.strip())
    return Settings(
        active_indices=indices,
        poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS",60),
        trade_cooldown_seconds=_get_int("TRADE_COOLDOWN_SECONDS",900),
        max_trades_per_day=_get_int("MAX_TRADES_PER_DAY",3),
        stop_loss=_get_float("STOP_LOSS",-1500.0),
        target_profit=_get_float("TARGET_PROFIT",1000.0),
        target_delta=_get_float("TARGET_DELTA",0.30),
        # NEW: minimum credit-to-spread ratio for trade quality
        # 0.25 = credit must be >= 25% of spread width
        # e.g. 200pt spread needs >= Rs50/share credit
        # This ensures R:R is at least 1:3 (profit:loss)
        min_credit_ratio=_get_float("MIN_CREDIT_RATIO", 0.25),
        delta_hedge_threshold=_get_float("DELTA_HEDGE_THRESHOLD",0.05),
        risk_free_rate=_get_float("RISK_FREE_RATE",0.06),
        fallback_time_to_expiry_years=_get_float("FALLBACK_TIME_TO_EXPIRY_YEARS",0.1),
        min_time_to_expiry_days=_get_float("MIN_TIME_TO_EXPIRY_DAYS",1.0),
        spread_width_points=_get_int("SPREAD_WIDTH_POINTS",200),
        price_scan_range=_get_float("PRICE_SCAN_RANGE",0.065),
        exposure_margin_rate=_get_float("EXPOSURE_MARGIN_RATE",0.03),
        default_iv=_get_float("DEFAULT_IV",0.18),
        default_oi=_get_int("DEFAULT_OI",50000),
        min_oi=_get_int("MIN_OI",1),
        oi_score_window_points=_get_int("OI_SCORE_WINDOW_POINTS",300),
        oi_score_normalizer=_get_float("OI_SCORE_NORMALIZER",100000.0),
        market_open_time=_get_time("MARKET_OPEN_TIME","09:15"),
        market_close_time=_get_time("MARKET_CLOSE_TIME","15:30"),
        log_file=os.getenv("LOG_FILE","trading_bot.log"),
        redis_url=os.getenv("REDIS_URL","redis://localhost:6379"),
        model_path=os.path.join(os.path.dirname(__file__), "..", os.getenv("MODEL_PATH","models/xgb.pkl")),
        instrument_cache_file=os.getenv("INSTRUMENT_CACHE_FILE","instruments_cache.json"),
        instrument_cache_ttl_hours=_get_float("INSTRUMENT_CACHE_TTL_HOURS",12.0),
        instrument_master_url=os.getenv("INSTRUMENT_MASTER_URL","https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"),
        http_timeout_seconds=_get_int("HTTP_TIMEOUT_SECONDS",15),
        api_retries=_get_int("API_RETRIES",3),
        api_retry_sleep_seconds=_get_float("API_RETRY_SLEEP_SECONDS",1.0),
        quote_throttle_seconds=_get_float("QUOTE_THROTTLE_SECONDS",0.05),
        order_product=os.getenv("ORDER_PRODUCT","D"),
        order_validity=os.getenv("ORDER_VALIDITY","DAY"),
        order_tag=os.getenv("ORDER_TAG","algo_bot"),
        dry_run=_get_bool("DRY_RUN",True),
        regime_side_vol_threshold=_get_float("REGIME_SIDE_VOL_THRESHOLD",0.007),
        backtest_initial_capital=_get_float("BACKTEST_INITIAL_CAPITAL",500000.0),
        backtest_days=_get_int("BACKTEST_DAYS",365),
        backtest_interval=os.getenv("BACKTEST_INTERVAL","1d"),
        backtest_entry_every_bars=_get_int("BACKTEST_ENTRY_EVERY_BARS",5),
        backtest_holding_bars=_get_int("BACKTEST_HOLDING_BARS",10),
        backtest_commission_per_order=_get_float("BACKTEST_COMMISSION_PER_ORDER",20.0),
        backtest_slippage_bps=_get_float("BACKTEST_SLIPPAGE_BPS",2.0),
        backtest_strike_step=_get_int("BACKTEST_STRIKE_STEP",50),
        backtest_chain_width_points=_get_int("BACKTEST_CHAIN_WIDTH_POINTS",1000),
        backtest_stop_loss=_get_float("BACKTEST_STOP_LOSS",-1500.0),
        backtest_target_profit=_get_float("BACKTEST_TARGET_PROFIT",1000.0),
        backtest_output_dir=os.getenv("BACKTEST_OUTPUT_DIR","backtest_results"),
        backtest_option_data_path=os.getenv("BACKTEST_OPTION_DATA_PATH",""),
        backtest_option_data_format=os.getenv("BACKTEST_OPTION_DATA_FORMAT","auto").lower(),
        backtest_option_timestamp_tolerance_minutes=_get_int("BACKTEST_OPTION_TIMESTAMP_TOLERANCE_MINUTES",30),
        backtest_allow_synthetic_fallback=_get_bool("BACKTEST_ALLOW_SYNTHETIC_FALLBACK",True),
    )

INDEX_CONFIG = _load_index_config()
SETTINGS     = load_settings()
INDEX        = SETTINGS.active_indices[0]
LOT_SIZE     = int(INDEX_CONFIG.get(INDEX,{}).get("lot_size",1))
TARGET_DELTA = SETTINGS.target_delta
DELTA_HEDGE_THRESHOLD = SETTINGS.delta_hedge_threshold
RISK_FREE    = SETTINGS.risk_free_rate
EXPIRY_DAYS  = int(SETTINGS.min_time_to_expiry_days)
REDIS_URL    = SETTINGS.redis_url
