class PaperEngine:
    def __init__(self):
        self.positions = []   # list of trades
        self.realized = 0

    # =========================================
    # ADD POSITION (MULTI-LEG)
    # =========================================
    def add_position(self, strategy, legs):
        """
        legs = [
            {"side": "SELL", "strike": 22500, "type": "CE", "price": 100}
        ]
        """

        position = {
            "strategy": strategy,
            "legs": legs,
            "open": True,
            "pnl": 0
        }

        self.positions.append(position)

        print(f"Trade added: {strategy}")

    # =========================================
    # CHECK OPEN POSITIONS
    # =========================================
    def has_open_positions(self):
        return any(p["open"] for p in self.positions)

    # =========================================
    # MARK TO MARKET (REALISTIC)
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

                if leg["side"] == "BUY":
                    pnl += (ltp - entry)
                else:  # SELL
                    pnl += (entry - ltp)

            pos["pnl"] = pnl
            total_unrealized += pnl

        return total_unrealized

    # =========================================
    # GET PnL SUMMARY (🔥 THIS WAS MISSING)
    # =========================================
    def get_pnl(self):
        unrealized = sum(p["pnl"] for p in self.positions if p["open"])

        return {
            "realized": self.realized,
            "unrealized": unrealized,
            "total": self.realized + unrealized,
            "open_positions": sum(1 for p in self.positions if p["open"])
        }

    # =========================================
    # OPTIONAL: CLOSE ALL POSITIONS
    # =========================================
    def close_all(self):
        for pos in self.positions:
            if pos["open"]:
                self.realized += pos["pnl"]
                pos["open"] = False

    # =========================================
    # INTERNAL HELPER
    # =========================================
    def _get_ltp(self, chain, strike, option_type):
        for row in chain:
            if row["strikePrice"] == strike:
                return row[option_type]["ltp"]
        return None