"""Trade analytics that need candles: MAE / MFE, efficiency, R multiples."""

from typing import Any

APPROX_NOTE = "approximate: from bar high/low (bid prices); the entry and exit bars are counted whole"


def excursions(trade: dict[str, Any], rows: list[list], tf: str, tf_seconds: int,
               info: dict[str, Any]) -> dict[str, Any] | None:
    """Maximum adverse / favorable excursion of a trade, from candles that cover it.

    rows are raw EA rows: [time, open, high, low, close, tick_volume, spread].
    """
    open_ts = trade.get("_open_ts")
    end_ts = trade.get("_close_ts") or trade.get("_now_ts")
    open_price = trade.get("open_price")
    if open_ts is None or end_ts is None or open_price is None:
        return None
    inside = [r for r in rows if r[0] + tf_seconds > open_ts and r[0] <= end_ts]
    if not inside:
        return None

    high = max(r[2] for r in inside)
    low = min(r[3] for r in inside)
    buy = trade["side"] == "buy"
    favorable = max(high - open_price if buy else open_price - low, 0.0)
    adverse = max(open_price - low if buy else high - open_price, 0.0)

    digits = info.get("digits", 5)
    point = info.get("point") or 0.0
    tick_size = info.get("tick_size") or 0.0
    tick_value = info.get("tick_value") or 0.0
    volume = trade.get("volume") or 0.0

    def points(dist: float) -> float | None:
        return round(dist / point, 1) if point else None

    def money(dist: float) -> float | None:
        return round(dist / tick_size * tick_value * volume, 2) if tick_size and tick_value else None

    result: dict[str, Any] = {
        "tf": tf,
        "mae_price": round(adverse, digits),
        "mae_points": points(adverse),
        "mae_money": money(adverse),
        "mfe_price": round(favorable, digits),
        "mfe_points": points(favorable),
        "mfe_money": money(favorable),
    }

    exit_price = trade.get("close_price")
    if exit_price is None and trade.get("status") == "open":
        exit_price = trade.get("_current_price")
    captured = None
    if exit_price is not None:
        captured = (exit_price - open_price) if buy else (open_price - exit_price)
        result["captured_points"] = points(captured)
        # How far price came back from the best point before the exit.
        result["gave_back_points"] = points(favorable - captured)
        # Share of the favorable move that was kept; meaningless for losing trades.
        result["efficiency"] = round(captured / favorable, 2) if favorable > 0 and captured > 0 else None

    sl = trade.get("sl")
    if sl:
        risk = abs(open_price - sl)
        if risk > 0:
            result["risk_points"] = points(risk)
            result["mae_r"] = round(adverse / risk, 2)
            result["mfe_r"] = round(favorable / risk, 2)
            if captured is not None:
                result["r_multiple"] = round(captured / risk, 2)

    result["note"] = APPROX_NOTE
    return result
