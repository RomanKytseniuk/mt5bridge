"""High-level API shared by the CLI and the MCP server. Every public method returns JSON-ready data."""

import time
from pathlib import Path
from typing import Any

from . import analysis, chart
from . import stats as stats_mod
from .client import BridgeClient
from .errors import BridgeError, RemoteError
from .install import packaged_ea_version, version_tuple
from .terminal import EA_NAME, USER_STEPS, install_dir, is_running
from .timeframes import TF_SECONDS, auto_tf, normalize_tf
from .timeutil import fmt_duration, fmt_time, parse_time
from .trades import build_trades, positions_missing_entry, public, short_enum

DEFAULT_LOOKBACK_S = 30 * 86400
EXCURSION_LADDER = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
EXCURSION_MAX_BARS = 5000
CANDLE_COLS = ["time", "open", "high", "low", "close", "volume"]
TIMES_NOTE = "all times are broker server time, as shown on the MT5 chart"


def _utc_offset(server_ts: int, gmt_ts: int) -> str:
    off = round((server_ts - gmt_ts) / 1800) * 1800
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return f"{sign}{off // 3600:02d}:{off % 3600 // 60:02d}"


def _next_step(st: dict[str, Any]) -> str | None:
    """What has to happen before the bridge works, in words an agent can pass on."""
    if not st["ea_running"]:
        mql5 = Path(st["bridge_dir"]).parent.parent
        if not (mql5 / "Experts" / f"{EA_NAME}.ex5").exists():
            return "Run `mt5bridge setup`: it installs the EA and starts MT5 with it."
        install = install_dir(mql5.parent)
        if install is not None and not is_running(install):
            return f"MT5 is closed: run `mt5bridge setup` to start it with {EA_NAME} on a chart."
        steps = " ".join(f"{i}. {s}" for i, s in enumerate(USER_STEPS, 1))
        if install is not None:
            return f"MT5 is open but {EA_NAME} is not on a chart. Ask the user: {steps}"
        return (f"Start MT5 with {EA_NAME} on a chart: if MT5 is closed, run `mt5bridge setup`; "
                f"if it is open, ask the user: {steps}")
    running = (st.get("heartbeat") or {}).get("ea_version") or ""
    shipped = packaged_ea_version() or ""
    if version_tuple(running) and version_tuple(shipped) and version_tuple(running) < version_tuple(shipped):
        return (f"The running EA is {running}, this mt5bridge ships {shipped}: ask the user to close MT5, "
                "then run `mt5bridge setup` (it updates the EA and starts MT5 with it).")
    return None


def symbol_matches(name: str, query: str) -> bool:
    """Case-insensitive; "EURUSD" also matches broker variants like "EURUSD.s" or "EURUSD+"
    (but not "EURUSDX": a suffix must not start with a letter)."""
    n, q = name.upper(), query.strip().upper()
    if n == q:
        return True
    return n.startswith(q) and len(n) > len(q) and not n[len(q)].isalpha()


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Bridge:
    def __init__(self, client: BridgeClient | None = None, *, bridge_dir: str | None = None, timeout: float = 15.0):
        self.client = client or BridgeClient(bridge_dir, timeout=timeout)
        self._symbols: dict[str, dict[str, Any]] = {}
        self._now: tuple[float, int] | None = None

    # ------------------------------------------------------------------ basics
    def status(self) -> dict[str, Any]:
        result = self.client.heartbeat()
        if result["ea_running"]:
            try:
                ping = self.client.request("ping")
                ping["utc_offset"] = _utc_offset(ping["server_time"], ping["gmt_time"])
                ping["server_time"] = fmt_time(ping["server_time"])
                for key in ("gmt_time", "local_time"):
                    ping.pop(key, None)
                result["terminal"] = ping
            except BridgeError as exc:
                result["error"] = str(exc)
        if result.get("heartbeat"):
            result["heartbeat"].pop("server_time", None)
        result["next"] = _next_step(result)
        return result

    def server_now(self) -> int:
        if self._now is not None:
            started, value = self._now
            elapsed = time.monotonic() - started
            if elapsed < 30:
                return value + int(elapsed)
        ping = self.client.request("ping")
        self._now = (time.monotonic(), int(ping["server_time"]))
        return self._now[1]

    def account(self) -> dict[str, Any]:
        acc = self.client.request("account")
        acc["trade_mode"] = short_enum(acc["trade_mode"], "ACCOUNT_TRADE_MODE_")
        acc["margin_mode"] = short_enum(acc["margin_mode"], "ACCOUNT_MARGIN_MODE_")
        acc["server_time"] = fmt_time(acc["server_time"])
        return acc

    def symbol(self, symbol: str) -> dict[str, Any]:
        """Symbol specification plus the latest quote."""
        info = self.client.request("symbol", symbol=symbol)
        self._symbols[symbol] = info
        result = dict(info)
        result["tick_time"] = fmt_time(info["tick_time_msc"] / 1000) if info.get("tick_time_msc") else None
        result.pop("tick_time_msc", None)
        return result

    def _symbol_info(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._symbols:
            self._symbols[symbol] = self.client.request("symbol", symbol=symbol)
        return self._symbols[symbol]

    # ------------------------------------------------------------------ candles
    def _rates(self, symbol: str, tf: str, *, frm: int | None = None, to: int | None = None,
               count: int | None = None) -> dict[str, Any]:
        params = {"symbol": symbol, "tf": tf, "from": frm, "to": to, "count": count}
        for attempt in range(4):
            try:
                return self.client.request("rates", **params)
            except RemoteError as exc:
                if not str(exc).startswith("history_loading") or attempt == 3:
                    raise
                time.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable")

    @staticmethod
    def _compact(symbol: str, tf: str, rows: list[list], truncated: bool) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "tf": tf,
            "count": len(rows),
            "truncated": truncated,
            "cols": CANDLE_COLS,
            "rows": [[fmt_time(r[0], seconds=False), r[1], r[2], r[3], r[4], r[5]] for r in rows],
        }

    def candles(self, symbol: str, timeframe: str = "H1", date_from: str | None = None,
                date_to: str | None = None, count: int | None = None, max_bars: int = 500) -> dict[str, Any]:
        tf = normalize_tf(timeframe)
        now = self.server_now()
        frm = parse_time(date_from, now)
        to = parse_time(date_to, now, end_of_day=True)
        if frm is None and not count:
            count = min(200, max_bars)
        data = self._rates(symbol, tf, frm=frm, to=to, count=min(count, max_bars) if count else None)
        rows, truncated = data["rows"], bool(data.get("truncated"))
        if len(rows) > max_bars:
            rows, truncated = rows[-max_bars:], True
        result = self._compact(data["symbol"], tf, rows, truncated)
        if truncated:
            result["warning"] = f"only the last {len(rows)} candles are returned; narrow the range or use a larger timeframe"
        return result

    # ------------------------------------------------------------------ trades
    def _range(self, date_from: str | None, date_to: str | None, lookback: int = DEFAULT_LOOKBACK_S) -> tuple[int, int]:
        now = self.server_now()
        to = parse_time(date_to, now, end_of_day=True) if date_to else now + 60
        frm = parse_time(date_from, now) if date_from else to - lookback
        if frm > to:
            raise BridgeError("date_from is after date_to")
        return frm, to

    def _deals(self, frm: int, to: int) -> list[dict[str, Any]]:
        deals = self.client.request("deals", **{"from": frm, "to": to})["deals"]
        # Positions closed in range but opened earlier: fetch their whole history.
        missing = positions_missing_entry(deals)
        # Positions still open at `to` might have closed later: fetch those too.
        if to < self.server_now():
            built, _ = build_trades(deals)
            missing += [t["position_id"] for t in built if t["status"] == "open"]
        for chunk in _chunks(sorted(set(missing)), 200):
            deals += self.client.request("position_deals", ids=",".join(map(str, chunk)))["deals"]
        return deals

    def _collect(self, date_from: str | None, date_to: str | None, symbol: str | None = None,
                 side: str | None = None, include_open: bool = False) -> tuple[list[dict], list[dict], int, int]:
        frm, to = self._range(date_from, date_to)
        trades, cash = build_trades(self._deals(frm, to))
        selected = []
        for t in trades:
            if t["status"] == "open":
                if not include_open:
                    continue
            elif t["_close_ts"] is None or not frm <= t["_close_ts"] <= to:
                continue
            if symbol and not symbol_matches(t["symbol"], symbol):
                continue
            if side and t["side"] != side.lower():
                continue
            selected.append(t)
        return selected, cash, frm, to

    def raw_deals(self, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        frm, to = self._range(date_from, date_to)
        deals = self.client.request("deals", **{"from": frm, "to": to})["deals"]
        for d in deals:
            d["time"] = fmt_time(d.pop("time_msc") / 1000)
        return {"from": fmt_time(frm), "to": fmt_time(to), "count": len(deals), "deals": deals}

    def trades(self, date_from: str | None = None, date_to: str | None = None, symbol: str | None = None,
               side: str | None = None, limit: int | None = 100, include_open: bool = False) -> dict[str, Any]:
        selected, cash, frm, to = self._collect(date_from, date_to, symbol, side, include_open)
        selected.sort(key=lambda t: t["_close_ts"] or t["_open_ts"] or 0, reverse=True)
        total = len(selected)
        if limit:
            selected = selected[:limit]
        result = {
            "from": fmt_time(frm),
            "to": fmt_time(to),
            "total": total,
            "returned": len(selected),
            "order": "newest first",
            "note": TIMES_NOTE,
            "trades": [public(t) for t in selected],
        }
        if cash:
            result["cash"] = cash
        return result

    def stats(self, date_from: str | None = None, date_to: str | None = None, by: str | None = None,
              symbol: str | None = None) -> dict[str, Any]:
        selected, _, frm, to = self._collect(date_from, date_to, symbol)
        result = stats_mod.compute(selected, by)
        return {"from": fmt_time(frm), "to": fmt_time(to), **result}

    # ------------------------------------------------------------------ one trade
    def _find_trade(self, trade_id: str | int) -> dict[str, Any]:
        tid = str(trade_id).strip().lstrip("#")
        try:
            pid = int(tid.split("-")[0])
        except ValueError:
            raise BridgeError(f"bad trade id {trade_id!r}: expected a position id like 123456 or 123456-2") from None
        trades, _ = build_trades(self.client.request("position_deals", ids=str(pid))["deals"])
        for t in trades:
            if t["id"] == tid:
                return t
        raise BridgeError(f"trade {tid} not found in this account's history")

    def _trade_bundle(self, trade_id: str | int, timeframe: str | None, bars_before: int, bars_after: int,
                      max_bars: int) -> dict[str, Any]:
        t = self._find_trade(trade_id)
        try:
            return self._trade_market_data(t, timeframe, bars_before, bars_after, max_bars)
        except RemoteError as exc:
            # Old trades on symbols the broker has since removed (e.g. EURUSD+ -> EURUSD.s).
            return {"trade": t, "excursions": None, "tf": None, "rows": [], "truncated": False,
                    "entry_index": None, "exit_index": None,
                    "unavailable": f"no market data for {t['symbol']}: {exc}. The broker may have removed this symbol."}

    def _trade_market_data(self, t: dict[str, Any], timeframe: str | None, bars_before: int, bars_after: int,
                           max_bars: int) -> dict[str, Any]:
        info = self._symbol_info(t["symbol"])
        now = self.server_now()
        if t["status"] == "open":
            quote = self.client.request("symbol", symbol=t["symbol"])
            t["_now_ts"] = now
            t["_current_price"] = quote.get("bid") if t["side"] == "buy" else quote.get("ask")
        end_ts = t["_close_ts"] or now
        duration = max(end_ts - t["_open_ts"], 1)

        ex_tf = next((tf for tf in EXCURSION_LADDER if duration / TF_SECONDS[tf] <= EXCURSION_MAX_BARS), "D1")
        try:
            ex_rows = self._rates(t["symbol"], ex_tf, frm=int(t["_open_ts"]) - TF_SECONDS[ex_tf], to=int(end_ts))["rows"]
            excursions = analysis.excursions(t, ex_rows, ex_tf, TF_SECONDS[ex_tf], info)
        except RemoteError as exc:
            excursions = {"error": str(exc)}

        target = max(10, min(80, max_bars - bars_before - bars_after))
        tf = normalize_tf(timeframe) if timeframe else auto_tf(duration, target)
        sec = TF_SECONDS[tf]
        # Over-fetch around the trade (weekends, gaps), then cut exact bar counts.
        rows = self._rates(t["symbol"], tf, frm=int(t["_open_ts"]) - 3 * bars_before * sec - sec,
                           to=int(end_ts) + 3 * bars_after * sec + sec)["rows"]
        i_in = max((i for i, r in enumerate(rows) if r[0] <= t["_open_ts"]), default=0)
        i_out = max((i for i, r in enumerate(rows) if r[0] <= end_ts), default=len(rows) - 1)
        start = max(0, i_in - bars_before)
        stop = min(len(rows), i_out + bars_after + 1)
        rows = rows[start:stop]
        truncated = len(rows) > max_bars
        if truncated:
            rows = rows[:max_bars]
        return {"trade": t, "excursions": excursions, "tf": tf, "rows": rows, "truncated": truncated,
                "entry_index": i_in - start, "exit_index": i_out - start, "digits": info.get("digits")}

    def trade(self, trade_id: str | int, timeframe: str | None = None, bars_before: int = 30,
              bars_after: int = 15, max_bars: int = 300, include_candles: bool = True) -> dict[str, Any]:
        b = self._trade_bundle(trade_id, timeframe, bars_before, bars_after, max_bars)
        t = b["trade"]
        result: dict[str, Any] = {"trade": public(t), "excursions": b["excursions"], "note": TIMES_NOTE}
        if "unavailable" in b:
            result["market_data"] = b["unavailable"]
        elif include_candles:
            candles = self._compact(t["symbol"], b["tf"], b["rows"], b["truncated"])
            candles["entry_index"] = b["entry_index"]
            candles["exit_index"] = b["exit_index"] if t["status"] != "open" else None
            if b["truncated"]:
                candles["warning"] = "candles cut at max_bars; use a larger timeframe to see the whole trade"
            result["candles"] = candles
        return result

    # ------------------------------------------------------------------ live
    def live(self) -> dict[str, Any]:
        acc = self.account()
        positions = self.client.request("positions")["positions"]
        now = self.server_now()
        items = []
        for p in positions:
            opened = p["time_msc"] / 1000
            items.append({
                "id": str(p["position"]),
                "symbol": p["symbol"],
                "side": "buy" if p["type"] == "POSITION_TYPE_BUY" else "sell",
                "volume": p["volume"],
                "open_time": fmt_time(opened),
                "open_price": p["price_open"],
                "current_price": p["price_current"],
                "sl": p["sl"] or None,
                "tp": p["tp"] or None,
                "profit": p["profit"],
                "swap": p["swap"],
                "age": fmt_duration(now - opened),
                "magic": p["magic"],
                "comment": p["comment"],
            })
        keys = ("login", "server", "currency", "trade_mode", "balance", "equity", "margin", "margin_free", "margin_level")
        return {
            "server_time": fmt_time(now),
            "account": {k: acc[k] for k in keys},
            "floating": round(sum(p["profit"] + p["swap"] for p in positions), 2),
            "positions": items,
        }

    # ------------------------------------------------------------------ charts
    def chart(self, trade_id: str | int | None = None, symbol: str | None = None, timeframe: str | None = None,
              date_from: str | None = None, date_to: str | None = None, count: int = 150,
              bars_before: int = 30, bars_after: int = 15, volume: bool = False) -> bytes:
        if trade_id is not None:
            b = self._trade_bundle(trade_id, timeframe, bars_before, bars_after, max(count, 60))
            if "unavailable" in b:
                raise BridgeError(b["unavailable"])
            return chart.render(b["rows"], symbol=b["trade"]["symbol"], tf=b["tf"], trade=b["trade"],
                                digits=b.get("digits"), excursions=b["excursions"], volume=volume)
        if not symbol:
            raise BridgeError("pass a trade id, or a symbol")
        tf = normalize_tf(timeframe or "H1")
        now = self.server_now()
        frm = parse_time(date_from, now)
        to = parse_time(date_to, now, end_of_day=True)
        data = self._rates(symbol, tf, frm=frm, to=to, count=None if frm else count)
        return chart.render(data["rows"][-max(count, 1):] if not frm else data["rows"], symbol=data["symbol"], tf=tf,
                            digits=data.get("digits"), volume=volume)
