# core/broker/kotak.py

class Broker:
    def __init__(self):
        print("✅ Kotak Broker initialized (stub)")

    def place_order(self, *args, **kwargs):
        print("Order placed:", args, kwargs)