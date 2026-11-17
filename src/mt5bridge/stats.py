"""Journal statistics over closed trades."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .timeutil import fmt_duration

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


GROUPERS: dict[str, Callable[[dict], str]] = {
    "symbol": lambda t: t["symbol"],
    "side": lambda t: t["side"],
    "exit_reason": lambda t: t["exit_reason"] or "unknown",
    "weekday": lambda t: WEEKDAYS[_dt(t["_open_ts"]).weekday()],
    "hour": lambda t: f"{_dt(t['_open_ts']).hour:02d}",
    "day": lambda t: _dt(t["_close_ts"]).strftime("%Y-%m-%d"),
    "month": lambda t: _dt(t["_close_ts"]).strftime("%Y-%m"),
    "magic": lambda t: str(t["magic"]),
}


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)

    equity = peak = max_dd = 0.0
    streak = max_streak = 0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        streak = streak + 1 if x < 0 else 0
        max_streak = max(max_streak, streak)

    durations = [t["_duration_s"] for t in trades if t.get("_duration_s") is not None]
    count = len(nets)
    return {
        "trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / count, 3) if count else None,
        "net": round(sum(nets), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win": round(gross_profit / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "expectancy": round(sum(nets) / count, 2) if count else None,
        "best": max(nets) if nets else None,
        "worst": min(nets) if nets else None,
        "max_drawdown": round(max_dd, 2),
        "max_losing_streak": max_streak,
        "avg_duration": fmt_duration(sum(durations) / len(durations)) if durations else None,
        "volume": round(sum(t["volume"] for t in trades), 2),
        "costs": round(sum(t["commission"] + t["swap"] + t["fee"] for t in trades), 2),
    }


def compute(trades: list[dict[str, Any]], by: str | None = None) -> dict[str, Any]:
    closed = sorted((t for t in trades if t["status"] == "closed"), key=lambda t: t["_close_ts"])
    result: dict[str, Any] = {"overall": summarize(closed)}
    if by:
        if by not in GROUPERS:
            raise ValueError(f"unknown group {by!r}; use one of {', '.join(GROUPERS)}")
        groups: dict[str, list] = {}
        for t in closed:
            groups.setdefault(GROUPERS[by](t), []).append(t)
        rows = [{by: key, **summarize(items)} for key, items in groups.items()]
        natural = by in ("weekday", "hour", "day", "month")
        if by == "weekday":
            rows.sort(key=lambda r: WEEKDAYS.index(r[by]))
        elif natural:
            rows.sort(key=lambda r: r[by])
        else:
            rows.sort(key=lambda r: r["net"])
        result["by"] = by
        result["groups"] = rows
    return result
