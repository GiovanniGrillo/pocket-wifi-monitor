"""Shared, dependency-free signal helpers: EARFCN->band and a quality verdict.

Kept stdlib-only on purpose so it can be imported both on the PC (by
adb_signal.py) and on the phone (by phone_agent.py, under Termux, where heavy
packages may be absent).
"""
from __future__ import annotations

# DL EARFCN -> LTE band. The Alcatel MW40 reports the serving EARFCN in its
# "Band" field, and Termux's cellinfo gives an EARFCN too, so both reuse this.
# Ranges are the standard E-UTRA N_DL bounds. is_low flags sub-GHz bands
# (700/800/850/900) — the ones that reach far and go through walls, which is
# what decides coverage in a remote/mountain spot.
EARFCN_BANDS = [
    (0, 599, 1, "B1 (2100)", False),
    (600, 1199, 2, "B2 (1900)", False),
    (1200, 1949, 3, "B3 (1800)", False),
    (1950, 2399, 4, "B4 (AWS)", False),
    (2400, 2649, 5, "B5 (850)", True),
    (2750, 3449, 7, "B7 (2600)", False),
    (3450, 3799, 8, "B8 (900)", True),
    (5010, 5179, 12, "B12 (700)", True),
    (5180, 5279, 13, "B13 (700)", True),
    (5730, 5849, 17, "B17 (700)", True),
    (5850, 5999, 18, "B18 (850)", True),
    (6000, 6149, 19, "B19 (850)", True),
    (6150, 6449, 20, "B20 (800)", True),
    (6600, 7399, 21, "B21 (1500)", False),
    (9210, 9659, 28, "B28 (700)", True),
    (9770, 10359, 32, "B32 (1500 SDL)", False),
    (37750, 38249, 38, "B38 (2600 TDD)", False),
    (38650, 39649, 40, "B40 (2300 TDD)", False),
    (39650, 41589, 41, "B41 (2500 TDD)", False),
]


# Band number -> (label, is_low_band), for sources that report the band
# directly (e.g. Termux cellinfo "bands": [3]) rather than an EARFCN.
BAND_LABELS = {b: (label, low) for (_lo, _hi, b, label, low) in EARFCN_BANDS}


def band_label(n):
    """(label, is_low_band) from an LTE band NUMBER. Unknown -> ('Bn', None)."""
    if n is None:
        return "", None
    if n in BAND_LABELS:
        return BAND_LABELS[n]
    return f"B{n}", None


def decode_earfcn(value):
    """Map a DL EARFCN to (earfcn, band_number, label, is_low_band).

    Returns (None, None, "", None) for a missing value, and
    (value, None, "", None) for an EARFCN outside the known ranges.
    """
    if value is None:
        return None, None, "", None
    for lo, hi, band, label, low in EARFCN_BANDS:
        if lo <= value <= hi:
            return value, band, label, low
    return value, None, "", None


def classify_signal(rsrp, sinr):
    """Human verdict on LTE reception — the number the whole project is about.

    Primarily RSRP (level/coverage); SINR (quality) can only pull it down.
    Thresholds are the commonly used field-survey bands.
    """
    if rsrp is None:
        return ""
    if rsrp >= -80:
        level = "excellent"
    elif rsrp >= -90:
        level = "good"
    elif rsrp >= -100:
        level = "fair"
    elif rsrp >= -110:
        level = "poor"
    else:
        level = "very_poor"
    if sinr is not None:
        order = ["very_poor", "poor", "fair", "good", "excellent"]
        if sinr < 0 and order.index(level) > order.index("poor"):
            level = "poor"
        elif sinr < 5 and order.index(level) > order.index("fair"):
            level = "fair"
    return level
