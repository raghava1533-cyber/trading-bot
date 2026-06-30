"""
data/option_chain.py
Fetches option chain via Upstox broker (primary) or returns empty (no nsepython).
Use broker.get_option_chain() directly in most cases.
"""
import logging

log = logging.getLogger(__name__)


def get_option_chain(symbol: str = "NIFTY", broker=None,
                     range_size: int = 1000) -> tuple[list, float | None]:
    """
    Fetch option chain using the Upstox broker.
    Falls back to empty list if broker is unavailable.

    Returns: (chain, spot)  where chain is list of strike dicts.
    """
    if broker is None:
        log.warning("get_option_chain: no broker provided — returning empty chain")
        return [], None
    try:
        return broker.get_option_chain(symbol=symbol, range_size=range_size)
    except Exception as exc:
        log.error(f"get_option_chain: {exc}")
        return [], None
