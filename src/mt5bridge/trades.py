"""Turn raw MT5 deals into round-trip trades.

In MT5 a position is a sequence of deals that share DEAL_POSITION_ID: one or more entries,
one or more exits (partial closes), and on netting accounts a reversal deal (ENTRY_INOUT)
that closes the position and opens the opposite one in a single deal. Each run from flat to
flat becomes one trade here; a reversal starts a new trade with id "<position>-2", "-3", ...
"""

from typing import Any

from .timeutil import fmt_duration, fmt_time

EPS = 1e-9
SIDES = {"DEAL_TYPE_BUY": "buy", "DEAL_TYPE_SELL": "sell"}
OPPOSITE = {"buy": "sell", "sell": "buy"}


def short_enum(value: str, prefix: str) -> str:
    return (value[len(prefix):] if value.startswith(prefix) else value).lower()


def _none_if_zero(x: float | None) -> float | None:
    return None if not x else x


def _new_segment(pid: int, number: int, symbol: str, side: str) -> dict[str, Any]:
    return {
        "pid": pid, "number": number, "symbol": symbol, "side": side,
        "in_vol": 0.0, "in_pv": 0.0, "out_vol": 0.0, "out_pv": 0.0,
        "open_ts": None, "close_ts": None,
        "profit": 0.0, "commission": 0.0, "swap": 0.0, "fee": 0.0,
        "sl": None, "tp": None, "sl_exit": None, "tp_exit": None,
        "reason": None, "exits": 0, "magic": None, "comment": "", "incomplete": False,
    }


def _money(seg: dict, deal: dict, share: float, with_profit: bool) -> None:
    if with_profit:
        seg["profit"] += deal["profit"]
    seg["commission"] += deal["commission"] * share
    seg["swap"] += deal["swap"] * share
    seg["fee"] += deal.get("fee", 0.0) * share


def _add_in(seg: dict, deal: dict, volume: float, share: float = 1.0) -> None:
    ts = deal["time_msc"] / 1000
    if seg["open_ts"] is None:
        seg["open_ts"] = ts
        seg["sl"] = _none_if_zero(deal.get("sl"))
        seg["tp"] = _none_if_zero(deal.get("tp"))
        seg["magic"] = deal.get("magic")
        seg["comment"] = deal.get("comment", "")
    seg["in_vol"] += volume
    seg["in_pv"] += volume * deal["price"]
    _money(seg, deal, share, with_profit=False)


def _add_out(seg: dict, deal: dict, volume: float, share: float = 1.0) -> None:
    seg["out_vol"] += volume
    seg["out_pv"] += volume * deal["price"]
    seg["close_ts"] = deal["time_msc"] / 1000
    seg["sl_exit"] = _none_if_zero(deal.get("sl"))
    seg["tp_exit"] = _none_if_zero(deal.get("tp"))
    seg["reason"] = short_enum(deal.get("reason", ""), "DEAL_REASON_")
    seg["exits"] += 1
    _money(seg, deal, share, with_profit=True)


def _open_volume(seg: dict) -> float:
    return seg["in_vol"] - seg["out_vol"]


def _finish(seg: dict, closed: bool) -> dict[str, Any]:
    open_price = seg["in_pv"] / seg["in_vol"] if seg["in_vol"] > EPS else None
    close_price = seg["out_pv"] / seg["out_vol"] if seg["out_vol"] > EPS else None
    net = seg["profit"] + seg["commission"] + seg["swap"] + seg["fee"]
    duration = None
    if seg["open_ts"] is not None and seg["close_ts"] is not None and closed:
        duration = seg["close_ts"] - seg["open_ts"]
    tid = str(seg["pid"]) if seg["number"] == 1 else f"{seg['pid']}-{seg['number']}"
    status = "closed" if closed else "open"
    if seg["incomplete"]:
        status = "incomplete"
    return {
        "id": tid,
        "position_id": seg["pid"],
        "symbol": seg["symbol"],
        "side": seg["side"],
        "volume": round(seg["in_vol"] if seg["in_vol"] > EPS else seg["out_vol"], 8),
        "open_time": fmt_time(seg["open_ts"]),
        "open_price": None if open_price is None else round(open_price, 8),
        "close_time": fmt_time(seg["close_ts"]) if closed else None,
        "close_price": None if close_price is None or not closed else round(close_price, 8),
        "sl": seg["sl"],
        "tp": seg["tp"],
        "sl_at_exit": seg["sl_exit"] if closed else None,
        "tp_at_exit": seg["tp_exit"] if closed else None,
        "exit_reason": seg["reason"] if closed else None,
        "exits": seg["exits"],
        "profit": round(seg["profit"], 2),
        "commission": round(seg["commission"], 2),
        "swap": round(seg["swap"], 2),
        "fee": round(seg["fee"], 2),
        "net": round(net, 2),
        "duration": fmt_duration(duration),
        "status": status,
        "magic": seg["magic"],
        "comment": seg["comment"],
        "_open_ts": seg["open_ts"],
        "_close_ts": seg["close_ts"] if closed else None,
        "_duration_s": duration,
    }


def _walk_position(pid: int, deals: list[dict]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    seg: dict | None = None
    number = 0
    for d in deals:
        side = SIDES[d["type"]]
        entry = d["entry"]
        if entry == "DEAL_ENTRY_IN":
            if seg is None:
                number += 1
                seg = _new_segment(pid, number, d["symbol"], side)
            _add_in(seg, d, d["volume"])
        elif entry in ("DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY"):
            if seg is None:
                # The opening deal is outside the fetched history.
                number += 1
                seg = _new_segment(pid, number, d["symbol"], OPPOSITE[side])
                seg["incomplete"] = True
            _add_out(seg, d, d["volume"])
            if seg["incomplete"] or _open_volume(seg) <= EPS:
                trades.append(_finish(seg, closed=True))
                seg = None
        elif entry == "DEAL_ENTRY_INOUT":
            closing = max(_open_volume(seg), 0.0) if seg is not None else 0.0
            if seg is not None and closing > EPS:
                _add_out(seg, d, closing, share=closing / d["volume"])
                trades.append(_finish(seg, closed=True))
            seg = None
            rest = d["volume"] - closing
            if rest > EPS:
                number += 1
                seg = _new_segment(pid, number, d["symbol"], side)
                _add_in(seg, d, rest, share=rest / d["volume"])
    if seg is not None:
        trades.append(_finish(seg, closed=False))
    return trades


def build_trades(deals: list[dict]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (trades, cash operations). Trades are sorted by close (or open) time, oldest first."""
    by_position: dict[int, list[dict]] = {}
    cash: list[dict[str, Any]] = []
    seen: set[int] = set()
    for d in deals:
        if d["ticket"] in seen:
            continue
        seen.add(d["ticket"])
        if d["type"] in SIDES:
            by_position.setdefault(d["position"], []).append(d)
        else:
            cash.append({
                "ticket": d["ticket"],
                "time": fmt_time(d["time_msc"] / 1000),
                "type": short_enum(d["type"], "DEAL_TYPE_"),
                "amount": d["profit"],
                "comment": d.get("comment", ""),
            })
    trades: list[dict[str, Any]] = []
    for pid, items in by_position.items():
        items.sort(key=lambda x: (x["time_msc"], x["ticket"]))
        trades.extend(_walk_position(pid, items))
    trades.sort(key=lambda t: t["_close_ts"] or t["_open_ts"] or 0)
    return trades, cash


def positions_missing_entry(deals: list[dict]) -> list[int]:
    """Position ids that have an exit in `deals` but no entry: their opening is older."""
    has_in: set[int] = set()
    has_out: set[int] = set()
    for d in deals:
        if d["type"] not in SIDES:
            continue
        if d["entry"] == "DEAL_ENTRY_IN":
            has_in.add(d["position"])
        else:
            has_out.add(d["position"])
    return sorted(has_out - has_in)


def public(trade: dict[str, Any]) -> dict[str, Any]:
    """Drop internal fields (those starting with "_")."""
    return {k: v for k, v in trade.items() if not k.startswith("_")}
