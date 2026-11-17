"""MT5 timeframes."""

TF_SECONDS: dict[str, int] = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600, "M12": 720,
    "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}

# The ladder used when mt5bridge picks a timeframe by itself.
AUTO_LADDER = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")

_ALIASES = {"D": "D1", "W": "W1", "MN": "MN1", "1MO": "MN1", "1MN": "MN1"}


def normalize_tf(value: str) -> str:
    """Accept "H1", "h1", "1h", "60m", "1d", "D", "W1", "MN1" and return the MT5 name."""
    s = value.strip().upper()
    if s in TF_SECONDS:
        return s
    if s in _ALIASES:
        return _ALIASES[s]
    # "1H", "15M", "4H", "1D", "1W"
    if len(s) >= 2 and s[:-1].isdigit() and s[-1] in "MHDW":
        n, unit = int(s[:-1]), s[-1]
        if unit == "M" and n % 60 == 0 and f"H{n // 60}" in TF_SECONDS:
            return f"H{n // 60}"
        name = f"{unit}{n}"
        if name in TF_SECONDS:
            return name
    raise ValueError(f"unknown timeframe {value!r}; use one of {', '.join(TF_SECONDS)}")


def auto_tf(duration_s: float, target_bars: int) -> str:
    """Smallest timeframe from the ladder that shows the duration in at most target_bars bars."""
    for tf in AUTO_LADDER:
        if duration_s / TF_SECONDS[tf] <= target_bars:
            return tf
    return AUTO_LADDER[-1]
