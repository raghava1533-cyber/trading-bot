from data.candles import fetch_candles
from data.option_chain import get_option_chain
from ml.features import compute_features, label_regime
from ml.regime_xgb import train_xgb
from greeks.iv_surface import build_iv_surface
from greeks.engine import greeks_fd

FEATURES = ["ema_gap","rsi","vol","atr","bb_width"]

def main():
    print("Fetching candles...")
    df = fetch_candles()

    df = compute_features(df)
    df = label_regime(df)

    print("Training model...")
    model = train_xgb(df)

    print("Fetching option chain...")
    chain, spot = get_option_chain()

    iv_fn = build_iv_surface(chain)

    print("Computing Greeks...")

    for row in chain[:10]:
        K = row["strikePrice"]
        sigma = iv_fn(K)

        g = greeks_fd(
            S=spot,
            K=K,
            T=5/365,
            r=0.06,
            sigma=sigma,
            opt_type="CE"
        )

        print(f"Strike {K} Delta {g['delta']:.3f}")

    print("DONE")


if __name__ == "__main__":
    main()