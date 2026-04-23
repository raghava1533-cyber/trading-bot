class PaperEngine:
    def __init__(self):
        self.positions = []
        self.realized  = 0

    # =========================================
    # ADD POSITION
    # =========================================
    def add_position(self, strategy, legs, margin_info=None):
        self.positions.append({
            "strategy":    strategy,
            "legs":        legs,
            "open":        True,
            "pnl":         0,
            "margin_info": margin_info or {},
        })
        print(f"Trade added: {strategy}")

    # =========================================
    # HAS OPEN POSITIONS
    # blocks new trades until current is closed
    # =========================================
    def has_open_positions(self):
        return any(p["open"] for p in self.positions)

    # =========================================
    # MARK TO MARKET
    # =========================================
    def mark_to_market(self, chain):
        total_unrealized = 0
        for pos in self.positions:
            if not pos["open"]:
                continue
            pnl = 0
            for leg in pos["legs"]:
                ltp = self._get_ltp(chain, leg["strike"], leg["type"])
                if ltp is None:
                    continue
                entry = leg["price"]
                pnl  += (ltp - entry) if leg["side"] == "BUY" else (entry - ltp)
            pos["pnl"]         = pnl
            total_unrealized  += pnl
        return total_unrealized

    # =========================================
    # GET PnL SUMMARY
    # =========================================
    def get_pnl(self):
        unrealized = sum(p["pnl"] for p in self.positions if p["open"])
        return {
            "realized":       self.realized,
            "unrealized":     unrealized,
            "total":          self.realized + unrealized,
            "open_positions": sum(1 for p in self.positions if p["open"]),
        }

    # =========================================
    # GET MARGIN INFO (from latest open position)
    # =========================================
    def get_margin_info(self):
        for pos in reversed(self.positions):
            if pos["open"] and pos.get("margin_info"):
                return pos["margin_info"]
        return None

    # =========================================
    # CLOSE ALL OPEN POSITIONS
    # =========================================
    def close_all(self):
        for pos in self.positions:
            if pos["open"]:
                self.realized += pos["pnl"]
                pos["open"]    = False
        print("All positions closed")

    # =========================================
    # INTERNAL HELPER
    # =========================================
    def _get_ltp(self, chain, strike, option_type):
        for row in chain:
            if row["strikePrice"] == strike:
                return row[option_type].get("ltp")
        return None