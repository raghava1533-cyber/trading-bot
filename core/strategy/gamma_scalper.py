# strategy/gamma_scalper.py
def gamma_scalp(last_price, current_price, position, threshold=0.002):
    move = (current_price - last_price) / last_price

    if abs(move) > threshold:
        return True
    return False