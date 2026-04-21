import numpy as np
from scipy.interpolate import griddata

def build_iv_surface(chain):
    strikes = []
    ivs = []

    for row in chain:
        k = row["strikePrice"]
        for side in ["CE","PE"]:
            if row.get(side):
                iv = row[side].get("impliedVolatility", 0)
                if iv > 0:
                    strikes.append(k)
                    ivs.append(iv/100)

    strikes = np.array(strikes)
    ivs = np.array(ivs)

    # Smooth curve
    smooth = np.poly1d(np.polyfit(strikes, ivs, 3))

    return smooth