import json, logging, os, tempfile
from datetime import datetime

TRADE_HISTORY_FILE = os.path.join(tempfile.gettempdir(), "trade_history.json")

def load_trade_history():
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE,"r",encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logging.warning(f"load_trade_history: {exc}")
    return []

def _save_trade_history(history):
    try:
        with open(TRADE_HISTORY_FILE,"w",encoding="utf-8") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception as exc:
        logging.warning(f"_save_trade_history: {exc}")

class PaperEngine:
    def __init__(self):
        self.positions = []
        self.realized  = 0
        self._history  = load_trade_history()

    def add_position(self, strategy, legs, margin_info=None, index="NIFTY", entry_time=None):
        self.positions.append({
            "strategy":   strategy,
            "index":      index,
            "entry_time": entry_time or datetime.now().isoformat(timespec="seconds"),
            "legs":       legs,
            "open":       True,
            "pnl":        0,
            "margin_info": margin_info or {},
        })
        logging.info(f"Trade added: {strategy} [{index}]")

    def has_open_positions(self):
        return any(p["open"] for p in self.positions)

    def mark_to_market(self, chain):
        for pos in self.positions:
            if not pos["open"]: continue
            pnl = 0
            for leg in pos["legs"]:
                ltp = self._get_ltp(chain, leg["strike"], leg["type"])
                if ltp is None: continue
                entry = leg["price"]
                pnl  += (ltp-entry) if leg["side"]=="BUY" else (entry-ltp)
            pos["pnl"] = pnl

    def get_pnl(self):
        unrealized = sum(p["pnl"] for p in self.positions if p["open"])
        return {"realized":self.realized,"unrealized":unrealized,
                "total":self.realized+unrealized,
                "open_positions":sum(1 for p in self.positions if p["open"])}

    def get_margin_info(self):
        ms = [p["margin_info"] for p in self.positions if p["open"] and p.get("margin_info")]
        if not ms: return None
        if len(ms)==1: return ms[0]
        return {"credit_per_share":round(sum(m.get("credit_per_share",0) for m in ms),2),
                "net_credit":round(sum(m["net_credit"] for m in ms),2),
                "max_profit":round(sum(m["max_profit"] for m in ms),2),
                "max_loss":round(max(m["max_loss"] for m in ms),2),
                "margin_required":round(max(m["margin_required"] for m in ms),2),
                "peak_margin_est":round(sum(m.get("peak_margin_est",m["margin_required"]) for m in ms),2)}

    def close_all(self, exit_reason="MANUAL", carried_over=False):
        now = datetime.now().isoformat(timespec="seconds")
        for pos in self.positions:
            if pos["open"]:
                pnl = round(pos["pnl"], 2)
                self.realized += pnl
                pos["open"]    = False
                self._history.append({
                    "strategy":pos["strategy"],"index":pos.get("index","NIFTY"),
                    "entry_time":pos.get("entry_time","--"),"exit_time":now,
                    "exit_reason":exit_reason,"carried_over":carried_over,"pnl":pnl,
                    "legs":[{"side":l.get("side"),"type":l.get("type"),
                              "strike":l.get("strike"),"entry_price":l.get("price"),
                              "exit_ltp":l.get("ltp",0),"qty":l.get("qty",75),
                              "symbol":l.get("symbol","")} for l in pos.get("legs",[])]
                })
        _save_trade_history(self._history)
        logging.info(f"All positions closed — {exit_reason}")

    def _get_ltp(self, chain, strike, option_type):
        for row in chain:
            if row["strikePrice"] == strike:
                return row[option_type].get("ltp")
        return None
