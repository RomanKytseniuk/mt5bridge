"""mt5bridge command line. Every command prints JSON to stdout (errors too: {"ok": false, "error": ...})."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .errors import BridgeError
from .stats import GROUPERS

EPILOG = """
times: YYYY-MM-DD, "YYYY-MM-DD HH:MM", now, today, yesterday, or look-backs like 12h, 7d, 2w.
       All times are broker server time, the same as on the MT5 chart.
timeframes: M1 M5 M15 M30 H1 H4 D1 W1 MN1 (and M2..M20, H2..H12); 1h, 15m, 1d also work.
fields: exit_reason: sl, tp, so (stop out), client (manual), expert, mobile, web.
        net = profit + commission + swap + fee. MAE/MFE come from bar highs/lows (approximate).
        efficiency is for winning trades only; R values need an SL at entry. A "substitute" note
        means the broker removed the symbol (EURUSD+) and the candles come from the same instrument
        under its current name (EURUSD); a "market_data" note means there is no such instrument
        either, so there are no candles. `trades` returns "total" and
        "returned": when they differ, raise --limit (0 = all). `stats --by weekday|hour` uses the
        open time, `--by day|month` the close time. `chart` prints the path of the saved PNG.
terminals: with several MT5 terminals, `status` shows which one is used; pin it with --dir.

examples:
  mt5bridge status
  mt5bridge trades --from 7d
  mt5bridge trade 123456789 --tf M15
  mt5bridge candles XAUUSD H1 --count 100
  mt5bridge stats --from 2026-10-01 --by weekday
  mt5bridge chart 123456789 -o trade.png
  mt5bridge watch
  mt5bridge setup               install the EA + agent skill, start MT5 with the EA
"""


def _bridge(args: argparse.Namespace):
    from .service import Bridge

    return Bridge(bridge_dir=args.dir, timeout=args.timeout)


def _emit(data: Any, args: argparse.Namespace) -> None:
    pretty = args.pretty if args.pretty is not None else sys.stdout.isatty()
    text = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    sys.stdout.write(text + "\n")


def _watch(args: argparse.Namespace) -> int:
    bridge = _bridge(args)
    try:
        while True:
            data = bridge.live()
            if args.json:
                sys.stdout.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
            else:
                acc = data["account"]
                lines = [
                    f"{data['server_time']}  {acc['server']}  #{acc['login']}  ({acc['trade_mode']})",
                    f"balance {acc['balance']}  equity {acc['equity']}  floating {data['floating']}  "
                    f"margin level {acc['margin_level']}%  {acc['currency']}",
                    "",
                    f"{'id':>12} {'symbol':<10} {'side':<4} {'vol':>6} {'open':>12} {'now':>12} {'sl':>12} {'tp':>12} {'P/L':>10} {'age':>8}",
                ]
                for p in data["positions"]:
                    lines.append(
                        f"{p['id']:>12} {p['symbol']:<10} {p['side']:<4} {p['volume']:>6} {p['open_price']:>12} "
                        f"{p['current_price']:>12} {str(p['sl'] or '-'):>12} {str(p['tp'] or '-'):>12} "
                        f"{p['profit'] + p['swap']:>10.2f} {p['age']:>8}"
                    )
                if not data["positions"]:
                    lines.append("no open positions")
                sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(lines) + "\n\nCtrl+C to stop\n")
                sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mt5bridge",
        description="Read-only MetaTrader 5 bridge: trade journal, candles, MAE/MFE, live positions. JSON output.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"mt5bridge {__version__}")
    p.add_argument("--dir", help="bridge folder or <terminal>/MQL5/Files (default: auto-detect, or $MT5BRIDGE_DIR)")
    p.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for MT5 (default 15)")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--pretty", dest="pretty", action="store_true", default=None, help="indented JSON")
    fmt.add_argument("--compact", dest="pretty", action="store_false", help="one-line JSON (default when piped)")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    sub.add_parser("status", help="is MT5 running with the bridge EA attached")
    sub.add_parser("account", help="balance, equity, margin, account type")

    s = sub.add_parser("symbol", help="symbol specification and the latest quote")
    s.add_argument("symbol")

    s = sub.add_parser("trades", help="closed trades (entries and exits grouped per position)")
    s.add_argument("--from", dest="date_from", help="start (default: 30 days back)")
    s.add_argument("--to", dest="date_to", help="end (default: now)")
    s.add_argument("--symbol")
    s.add_argument("--side", choices=["buy", "sell"])
    s.add_argument("--limit", type=int, default=100, help="newest N trades (0 = all, default 100)")
    s.add_argument("--open", dest="include_open", action="store_true", help="include still open positions")

    s = sub.add_parser("trade", help="one trade with MAE/MFE and the candles around it")
    s.add_argument("id", help="trade id from `trades` (the position id)")
    s.add_argument("--tf", help="timeframe for candles (default: picked from trade duration)")
    s.add_argument("--before", type=int, default=30, help="candles before entry (default 30)")
    s.add_argument("--after", type=int, default=15, help="candles after exit (default 15)")
    s.add_argument("--max-bars", type=int, default=300)
    s.add_argument("--no-candles", action="store_true")

    s = sub.add_parser("candles", help="OHLCV candles on any timeframe")
    s.add_argument("symbol")
    s.add_argument("tf", nargs="?", default="H1")
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")
    s.add_argument("--count", type=int, help="last N candles (default 200 when --from is not given)")
    s.add_argument("--max-bars", type=int, default=500)

    sub.add_parser("live", help="open positions and account snapshot")

    s = sub.add_parser("watch", help="live view of open positions, refreshed every few seconds")
    s.add_argument("--interval", type=float, default=2.0)
    s.add_argument("--json", action="store_true", help="print one JSON line per refresh instead of a table")

    s = sub.add_parser("stats", help="journal statistics: win rate, profit factor, drawdown, ...")
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")
    s.add_argument("--by", choices=sorted(GROUPERS))
    s.add_argument("--symbol")

    s = sub.add_parser("chart", help="PNG chart of a trade, or of a symbol")
    s.add_argument("id", nargs="?", help="trade id (or use --symbol)")
    s.add_argument("--symbol")
    s.add_argument("--tf")
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")
    s.add_argument("--count", type=int, default=150)
    s.add_argument("--volume", action="store_true", help="add a tick-volume panel under the candles")
    s.add_argument("-o", "--output", help="PNG path (default: <id or symbol>.png)")

    s = sub.add_parser("deals", help="raw MT5 deals, ungrouped")
    s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to")

    s = sub.add_parser("setup", help="install/update the EA, install the agent skill, start MT5 with the EA")
    s.add_argument("--mql5", help="MQL5 folder of one terminal (default: every terminal found)")
    s.add_argument("--skills-dir", help="agent skills folder (default: ~/.claude/skills: Claude Code, OpenCode)")
    s.add_argument("--no-skill", action="store_true", help="do not install the agent skill")
    s.add_argument("--no-start", action="store_true", help="do not start MT5")
    s.add_argument("--wait", type=float, default=90.0, help="seconds to wait for the EA after starting MT5")

    s = sub.add_parser("mcp", help="run the MCP server over stdio")
    return p


def run(args: argparse.Namespace) -> Any:
    cmd = args.command
    if cmd == "setup":
        from .install import setup

        return setup(args.mql5, start=not args.no_start, skill=not args.no_skill,
                     skills_dir=args.skills_dir, wait=args.wait)
    if cmd == "status":
        from .client import BridgeClient
        from .service import Bridge

        return Bridge(BridgeClient(args.dir, timeout=args.timeout)).status()

    b = _bridge(args)
    if cmd == "account":
        return b.account()
    if cmd == "symbol":
        return b.symbol(args.symbol)
    if cmd == "trades":
        return b.trades(args.date_from, args.date_to, args.symbol, args.side, args.limit or None, args.include_open)
    if cmd == "trade":
        return b.trade(args.id, args.tf, args.before, args.after, args.max_bars, not args.no_candles)
    if cmd == "candles":
        return b.candles(args.symbol, args.tf, args.date_from, args.date_to, args.count, args.max_bars)
    if cmd == "live":
        return b.live()
    if cmd == "stats":
        return b.stats(args.date_from, args.date_to, args.by, args.symbol)
    if cmd == "deals":
        return b.raw_deals(args.date_from, args.date_to)
    if cmd == "chart":
        if not args.id and not args.symbol:
            raise BridgeError("pass a trade id or --symbol")
        png = b.chart(args.id, args.symbol, args.tf, args.date_from, args.date_to, args.count, volume=args.volume)
        out = Path(args.output or f"{args.id or args.symbol}.png").expanduser()
        out.write_bytes(png)
        return {"saved": str(out.resolve()), "bytes": len(png)}
    raise BridgeError(f"unknown command {cmd}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main(bridge_dir=args.dir, timeout=args.timeout)
        return 0
    if args.command == "watch":
        try:
            return _watch(args)
        except (BridgeError, ValueError) as exc:
            _emit({"ok": False, "error": str(exc)}, args)
            return 1
    try:
        _emit(run(args), args)
        return 0
    except (BridgeError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc)}, args)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
