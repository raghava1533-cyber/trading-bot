"""
execution/paper_engine.py
Tracks open positions, marks to market, computes P&L per leg and per position.
Writes trade history to disk on close.
"""
import json, logging, os, tempfile
from datetime import datetime, date

TRADE_HISTORY_FILE = os.path.join(tempfile.gettempdir(), "trade_history.json")
log = logging.getLogger(__name__)


def load_trade_history() -> list:
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        log.warning(f"load_trade_history: {exc}")
    return []


def _save_trade_history(history: list):
    try:
        with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception as exc:
        log.warning(f"_save_trade_history: {exc}")


class PaperEngine:
    def __init__(self):
        self.positions: list[dict] = []
        self.realized:  float      = 0.0
        self._history:  list       = load_trade_history()

    # ── Add position ──────────────────────────────────────────────────────────
    def add_position(self, strategy: str, legs: list, margin_info: dict = None,
                     index: str = "NIFTY", entry_time: str = None):
        """
        legs: list of dicts with keys:
          side, strike, type, price, symbol, ltp, qty, unrealized_pnl
        margin_info: dict with max_profit, max_loss, net_credit, margin_required, etc.
        """
        pos = {
            "strategy":    strategy,
            "index":       index,
            "entry_time":  entry_time or datetime.now().isoformat(timespec="seconds"),
            "legs":        legs,
            "open":        True,
            "unrealized":  0.0,
            "max_profit":  float((margin_info or {}).get("max_profit",  0)),
            "max_loss":    float((margin_info or {}).get("max_loss",    0)),
            "net_credit":  float((margin_info or {}).get("net_credit",  0)),
            "margin_info": margin_info or {},
        }
        self.positions.append(pos)
        log.info(f"Position added: {strategy} [{index}]  "
                 f"MaxProfit=Rs{pos['max_profit']:,.0f}  MaxLoss=Rs{pos['max_loss']:,.0f}")

    # ── Mark to market ────────────────────────────────────────────────────────
    def mark_to_market(self, chain: list):
        """Update LTP and unrealized P&L for every open leg."""
        for pos in self.positions:
            if not pos["open"]:
                continue
            pos_pnl = 0.0
            for leg in pos["legs"]:
                ltp = self._get_ltp(chain, leg["strike"], leg["type"])
                if ltp is None:
                    continue
                leg["ltp"] = round(ltp, 2)
                entry      = float(leg["price"])
                qty        = int(leg.get("qty", 75))
                if leg["side"] == "BUY":
                    leg_pnl = (ltp - entry) * qty
                else:
                    leg_pnl = (entry - ltp) * qty
                leg["unrealized_pnl"] = round(leg_pnl, 2)
                pos_pnl += leg_pnl
            pos["unrealized"] = round(pos_pnl, 2)

    # ── P&L summary ───────────────────────────────────────────────────────────
    def get_pnl(self) -> dict:
        open_pos   = [p for p in self.positions if p["open"]]
        unrealized = round(sum(p["unrealized"] for p in open_pos), 2)
        today_str  = date.today().isoformat()

        # Trades closed today
        today_closed = [
            h for h in self._history
            if str(h.get("exit_time", ""))[:10] == today_str
        ]
        today_realized = round(sum(h["pnl"] for h in today_closed), 2)
        today_trades   = len(today_closed)

        # Max profit / max loss across all open positions
        total_max_profit = round(sum(p.get("max_profit", 0) for p in open_pos), 2)
        total_max_loss   = round(sum(p.get("max_loss",   0) for p in open_pos), 2)
        total_net_credit = round(sum(p.get("net_credit", 0) for p in open_pos), 2)

        return {
            "realized":        self.realized,
            "unrealized":      unrealized,
            "total":           round(self.realized + unrealized, 2),
            "open_positions":  len(open_pos),
            # Today's stats
            "today_realized":  today_realized,
            "today_trades":    today_trades,
            # Risk metrics for open positions
            "max_profit":      total_max_profit,
            "max_loss":        total_max_loss,
            "net_credit":      total_net_credit,
        }

    def get_margin_info(self) -> dict | None:
        ms = [p["margin_info"] for p in self.positions
              if p["open"] and p.get("margin_info")]
        if not ms:
            return None
        if len(ms) == 1:
            return ms[0]
        return {
            "credit_per_share": round(sum(m.get("credit_per_share", 0) for m in ms), 2),
            "net_credit":       round(sum(m.get("net_credit",       0) for m in ms), 2),
            "max_profit":       round(sum(m.get("max_profit",       0) for m in ms), 2),
            "max_loss":         round(max(m.get("max_loss",         0) for m in ms), 2),
            "margin_required":  round(max(m.get("margin_required",  0) for m in ms), 2),
            "peak_margin_est":  round(sum(m.get("peak_margin_est",  0) for m in ms), 2),
        }

    # ── Close positions ───────────────────────────────────────────────────────
    def close_all(self, exit_reason: str = "MANUAL", carried_over: bool = False):
        now = datetime.now().isoformat(timespec="seconds")
        closed = 0
        for pos in self.positions:
            if not pos["open"]:
                continue
            pnl = round(pos["unrealized"], 2)
            self.realized  += pnl
            pos["open"]     = False
            pos["exit_time"] = now
            self._history.append({
                "strategy":    pos["strategy"],
                "index":       pos.get("index", "NIFTY"),
                "entry_time":  pos.get("entry_time", "--"),
                "exit_time":   now,
                "exit_reason": exit_reason,
                "carried_over": carried_over,
                "pnl":         pnl,
                "max_profit":  pos.get("max_profit", 0),
                "max_loss":    pos.get("max_loss",   0),
                "net_credit":  pos.get("net_credit", 0),
                "legs": [
                    {
                        "side":        l.get("side"),
                        "type":        l.get("type"),
                        "strike":      l.get("strike"),
                        "entry_price": l.get("price"),
                        "exit_ltp":    l.get("ltp", 0),
                        "qty":         l.get("qty", 75),
                        "symbol":      l.get("symbol", ""),
                        "leg_pnl":     l.get("unrealized_pnl", 0),
                    }
                    for l in pos.get("legs", [])
                ],
            })
            closed += 1
        _save_trade_history(self._history)
        log.info(f"Closed {closed} position(s) — reason: {exit_reason}  "
                 f"Realized: Rs{self.realized:+,.0f}")

    def has_open_positions(self) -> bool:
        return any(p["open"] for p in self.positions)

    def _get_ltp(self, chain: list, strike: float, option_type: str) -> float | None:
        for row in chain:
            if row["strikePrice"] == strike:
                return row[option_type].get("ltp")
        return None

    def to_dict(self) -> dict:
        """Serializable snapshot for state file / dashboard."""
        return {
            "positions":  self.positions,
            "realized":   self.realized,
            "pnl":        self.get_pnl(),
        }
