"""A stand-in for the MT5Bridge expert advisor: same file protocol, canned account data.

For tests of mt5bridge and of code built on it: `with FakeEA(bridge_dir) as ea: Bridge(bridge_dir=...)`."""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

TF_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600, "M12": 720,
    "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}


def ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


NOW = ts("2026-11-16 12:00")

SYMBOLS = {
    "EURUSD": {"digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0, "contract_size": 100000},
    "GBPUSD": {"digits": 5, "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0, "contract_size": 100000},
    "XAUUSD": {"digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0, "contract_size": 100},
}

# Price paths: piecewise linear through knots, flat outside them.
KNOTS = {
    "EURUSD": [(ts("2026-11-10 10:00"), 1.0700), (ts("2026-11-10 11:00"), 1.0690),
               (ts("2026-11-10 13:00"), 1.0760), (ts("2026-11-10 14:00"), 1.0750),
               (ts("2026-11-16 09:00"), 1.0800), (ts("2026-11-16 12:00"), 1.0820)],
    "GBPUSD": [(ts("2026-10-01 08:00"), 1.2500), (ts("2026-11-12 16:00"), 1.2600)],
    "XAUUSD": [(ts("2026-11-11 09:00"), 2650.0), (ts("2026-11-11 12:00"), 2655.0)],
}


def price_at(symbol: str, t: float) -> float:
    knots = KNOTS.get(symbol, [(0, 1.0)])
    if t <= knots[0][0]:
        return knots[0][1]
    for (t0, p0), (t1, p1) in zip(knots, knots[1:]):
        if t0 <= t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return knots[-1][1]


def deal(ticket, position, when, typ, entry, symbol, volume, price, profit=0.0, commission=0.0,
         reason="DEAL_REASON_CLIENT", sl=0.0, tp=0.0, comment=""):
    return {
        "ticket": ticket, "order": ticket, "position": position, "time_msc": ts(when) * 1000,
        "type": typ, "entry": entry, "reason": reason, "magic": 0, "symbol": symbol,
        "volume": volume, "price": price, "sl": sl, "tp": tp, "profit": profit,
        "commission": commission, "swap": 0.0, "fee": 0.0, "comment": comment,
    }


B, S = "DEAL_TYPE_BUY", "DEAL_TYPE_SELL"
IN, OUT, INOUT = "DEAL_ENTRY_IN", "DEAL_ENTRY_OUT", "DEAL_ENTRY_INOUT"

DEALS = [
    {**deal(1, 0, "2026-09-01 00:00", "DEAL_TYPE_BALANCE", IN, "", 0, 0, profit=10000.0, comment="deposit")},
    # 1003: GBPUSD buy opened long before the usual query range, closed by TP
    deal(30, 1003, "2026-10-01 08:00", B, IN, "GBPUSD", 0.10, 1.2500, commission=-0.7, sl=1.2400, tp=1.2600),
    # 1001: EURUSD buy, closed by TP
    deal(10, 1001, "2026-11-10 10:00", B, IN, "EURUSD", 0.10, 1.0700, commission=-0.7, sl=1.0680, tp=1.0750),
    deal(11, 1001, "2026-11-10 14:00", S, OUT, "EURUSD", 0.10, 1.0750, profit=50.0, commission=-0.7,
         reason="DEAL_REASON_TP", sl=1.0680, tp=1.0750),
    # 1002: XAUUSD sell, partial close by hand, rest stopped out
    deal(20, 1002, "2026-11-11 09:00", S, IN, "XAUUSD", 0.20, 2650.0, commission=-1.0, sl=2655.0),
    deal(21, 1002, "2026-11-11 10:00", B, OUT, "XAUUSD", 0.10, 2645.0, profit=50.0, commission=-0.5),
    deal(22, 1002, "2026-11-11 12:00", B, OUT, "XAUUSD", 0.10, 2655.0, profit=-50.0, commission=-0.5,
         reason="DEAL_REASON_SL", sl=2655.0),
    deal(31, 1003, "2026-11-12 16:00", S, OUT, "GBPUSD", 0.10, 1.2600, profit=100.0, commission=-0.7,
         reason="DEAL_REASON_TP"),
    # 1004: netting reversal: buy 0.1, reverse with sell 0.3, close the 0.2 sell
    deal(40, 1004, "2026-11-13 09:00", B, IN, "EURUSD", 0.10, 1.1000),
    deal(41, 1004, "2026-11-13 10:00", S, INOUT, "EURUSD", 0.30, 1.1100, profit=100.0, commission=-0.9),
    deal(42, 1004, "2026-11-13 11:00", B, OUT, "EURUSD", 0.20, 1.1050, profit=100.0),
    # 1005: still open
    deal(50, 1005, "2026-11-16 09:00", B, IN, "EURUSD", 0.05, 1.0800, commission=-0.35),
    # 1006: an old trade on a symbol the broker has since removed
    deal(60, 1006, "2026-10-05 10:00", B, IN, "GBPCHF+", 0.10, 1.1000),
    deal(61, 1006, "2026-10-05 12:00", S, OUT, "GBPCHF+", 0.10, 1.1010, profit=12.0),
]


def resolve(symbol: str) -> str:
    """Mirror of the EA's ResolveSymbol."""
    if symbol in SYMBOLS:
        return symbol
    want = symbol.upper()
    for name in SYMBOLS:
        if name.upper() == want:
            return name
    hits = [n for n in SYMBOLS if n.upper().startswith(want) and len(n) > len(want) and not n[len(want)].isalpha()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ValueError(f"ambiguous symbol {symbol}, pick one of: {', '.join(hits)}")
    raise ValueError(f"unknown symbol: {symbol}")

POSITIONS = [{
    "ticket": 50, "position": 1005, "symbol": "EURUSD", "type": "POSITION_TYPE_BUY", "volume": 0.05,
    "price_open": 1.08, "price_current": 1.082, "sl": 0.0, "tp": 1.09, "profit": 10.0, "swap": -0.2,
    "time_msc": ts("2026-11-16 09:00") * 1000, "magic": 0, "comment": "",
}]


class FakeEA:
    def __init__(self, root: Path, max_bars: int = 20000):
        self.root = root
        self.max_bars = max_bars
        self.req = root / "req"
        self.resp = root / "resp"
        self.req.mkdir(parents=True, exist_ok=True)
        self.resp.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.requests: list[dict] = []

    def __enter__(self):
        self._heartbeat()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        (self.root / "status.json").unlink(missing_ok=True)

    def _heartbeat(self):
        tmp = self.root / "status.tmp"
        tmp.write_text(json.dumps({"proto": 1, "ea_version": "fake", "chart_symbol": "EURUSD",
                                   "login": 42, "server": "Fake-Demo", "connected": True,
                                   "server_time": NOW, "data_path": str(self.root)}))
        os.replace(tmp, self.root / "status.json")

    def _loop(self):
        last = 0.0
        while not self._stop.is_set():
            for f in sorted(self.req.glob("*.req")):
                rid = f.stem
                params = dict(line.split("=", 1) for line in f.read_text("utf-8").splitlines() if "=" in line)
                f.unlink()
                self.requests.append(params)
                cmd = params.get("cmd", "")
                try:
                    payload = {"id": rid, "cmd": cmd, "data": self.handle(cmd, params), "ok": True}
                except Exception as exc:  # mirror EA: ok=false + error text
                    payload = {"id": rid, "cmd": cmd, "ok": False, "error": str(exc)}
                tmp = self.resp / f"{rid}.tmp"
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                os.replace(tmp, self.resp / f"{rid}.json")
            if time.monotonic() - last > 0.2:
                self._heartbeat()
                last = time.monotonic()
            time.sleep(0.005)

    # ------------------------------------------------------------- commands
    def handle(self, cmd: str, p: dict):
        if cmd == "ping":
            return {"proto": 1, "ea_version": "fake", "terminal_build": 5000, "connected": True,
                    "data_path": str(self.root), "server_time": NOW, "gmt_time": NOW - 7200,
                    "local_time": NOW - 7200, "login": 42, "server": "Fake-Demo"}
        if cmd == "account":
            return {"login": 42, "name": "Test", "server": "Fake-Demo", "company": "Fake Broker",
                    "currency": "USD", "leverage": 100, "trade_mode": "ACCOUNT_TRADE_MODE_DEMO",
                    "margin_mode": "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", "balance": 10250.0,
                    "equity": 10259.8, "profit": 9.8, "margin": 10.8, "margin_free": 10249.0,
                    "margin_level": 94998.15, "server_time": NOW}
        if cmd == "deals":
            frm, to = int(p.get("from", 0)), int(p.get("to", 0)) or NOW + 86400
            return {"deals": [d for d in DEALS if frm <= d["time_msc"] // 1000 <= to]}
        if cmd == "position_deals":
            ids = {int(x) for x in p["ids"].split(",") if x}
            return {"deals": [d for d in DEALS if d["position"] in ids]}
        if cmd == "positions":
            return {"positions": POSITIONS}
        if cmd == "symbol":
            sym = resolve(p.get("symbol", ""))
            info = SYMBOLS[sym]
            bid = round(price_at(sym, NOW), info["digits"])
            return {"symbol": sym, "description": sym, **info, "currency_base": sym[:3],
                    "currency_profit": sym[3:], "volume_min": 0.01, "volume_step": 0.01, "spread": 10,
                    "bid": bid, "ask": round(bid + 10 * info["point"], info["digits"]), "tick_time_msc": NOW * 1000}
        if cmd == "rates":
            return self.rates(p)
        raise ValueError(f"unknown command: {cmd}")

    def rates(self, p: dict):
        sym, tf = resolve(p.get("symbol", "")), p.get("tf", "H1")
        if tf not in TF_SECONDS:
            raise ValueError(f"unknown timeframe: {tf}")
        sec = TF_SECONDS[tf]
        frm, to, count = int(p.get("from", 0)), int(p.get("to", 0)), int(p.get("count", 0)) or 100
        count = min(count, self.max_bars)
        if frm:
            end = to or NOW
            times = list(range(-(-frm // sec) * sec, end + 1, sec))
        else:
            last = ((to or NOW) // sec) * sec
            times = [last - i * sec for i in range(count)][::-1]
        times = [t for t in times if t <= NOW]
        truncated = len(times) > self.max_bars
        times = times[-self.max_bars:]
        d = SYMBOLS[sym]["digits"]
        rows = []
        for t in times:
            o, c = price_at(sym, t), price_at(sym, t + sec)
            inner = [price_at(sym, k) for k, _ in KNOTS.get(sym, []) if t <= k <= t + sec]
            hi, lo = max([o, c, *inner]), min([o, c, *inner])
            rows.append([t, round(o, d), round(hi, d), round(lo, d), round(c, d), 100, 10])
        return {"symbol": sym, "tf": tf, "digits": d, "truncated": truncated, "rows": rows}
