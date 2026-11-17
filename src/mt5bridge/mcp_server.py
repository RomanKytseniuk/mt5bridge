"""MCP server on top of the mt5bridge core. Read-only: no tool can trade.

Run:  mt5bridge mcp     (or: mt5bridge-mcp, python -m mt5bridge mcp)
"""

from typing import Any

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import __version__
from .errors import BridgeError
from .service import Bridge

INSTRUCTIONS = """\
Read-only access to the user's MetaTrader 5 terminal: closed trades (journal), one trade with
MAE/MFE and the candles around it, candles on any timeframe, open positions, statistics, charts.
No tool can open, modify or close orders.

- All times are broker server time, the same as on the MT5 chart. Dates accept YYYY-MM-DD,
  "YYYY-MM-DD HH:MM", now, today, yesterday, or look-backs like 12h, 7d, 2w.
- Start with list_trades or trade_stats; then get_trade for a specific trade.
- Candles come as {"cols": [...], "rows": [[...], ...]} to save tokens.
- get_trade picks the timeframe from the trade's duration unless you pass one.
- If a tool says the EA is not running, call status: its "next" field says what the user has to do
  (usually: open MT5 and attach MT5Bridge to a chart).
- "net" = profit + commission + swap + fee. MAE/MFE come from bar highs/lows, so they are approximate.
- list_trades returns "total" and "returned": when they differ, the list is cut by `limit`.
- status names the terminal and account in use; with several terminals pass --dir when starting the server.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

_state: dict[str, Any] = {"bridge_dir": None, "timeout": 15.0, "bridge": None}


def _bridge() -> Bridge:
    b = _state["bridge"]
    if b is not None and _state["bridge_dir"] is None and not b.client.heartbeat()["ea_running"]:
        b = None  # nothing runs where we looked: the user may have attached the EA in another terminal
    if b is None:
        b = _state["bridge"] = Bridge(bridge_dir=_state["bridge_dir"], timeout=_state["timeout"])
    return b


async def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    def run() -> Any:
        return getattr(_bridge(), method)(*args, **kwargs)

    try:
        return await anyio.to_thread.run_sync(run)
    except (BridgeError, ValueError) as exc:
        # Anticipated failures: the model gets the message (e.g. "attach MT5Bridge to a chart").
        raise ToolError(str(exc)) from exc


server = MCPServer(
    name="mt5bridge",
    title="MetaTrader 5 (read-only)",
    instructions=INSTRUCTIONS,
    version=__version__,
)


@server.tool(annotations=READ_ONLY)
async def status() -> dict[str, Any]:
    """Check that MT5 is running with the MT5Bridge EA attached; shows terminal, account, server time and UTC offset."""
    return await _call("status")


@server.tool(annotations=READ_ONLY)
async def account() -> dict[str, Any]:
    """Account snapshot: balance, equity, margin, leverage, currency, demo/real, netting/hedging."""
    return await _call("account")


@server.tool(annotations=READ_ONLY)
async def list_trades(date_from: str | None = None, date_to: str | None = None, symbol: str | None = None,
                      side: str | None = None, limit: int = 50, include_open: bool = False) -> dict[str, Any]:
    """Closed trades, newest first. Each trade groups its entry and exit deals: prices, SL/TP at entry and
    at exit, exit reason (sl, tp, client, expert, so = stop out...), profit, commission, swap, fee, net
    (= profit + commission + swap + fee), duration.

    date_from/date_to default to the last 30 days. side is "buy" or "sell". limit=0 returns all;
    "total" vs "returned" shows whether the list was cut.
    """
    return await _call("trades", date_from, date_to, symbol, side, limit or None, include_open)


@server.tool(annotations=READ_ONLY)
async def get_trade(trade_id: str, timeframe: str | None = None, bars_before: int = 30, bars_after: int = 15,
                    max_bars: int = 300, include_candles: bool = True) -> dict[str, Any]:
    """One trade in depth: the trade, MAE/MFE (how far price went against / for it, in points, money and R),
    efficiency (captured / MFE), and candles around it with entry_index/exit_index into the rows.

    trade_id is the "id" from list_trades. timeframe is picked from the trade's duration if omitted.
    efficiency is given for winning trades only; R values only when the trade had an SL at entry.
    If the broker has removed the symbol, a "market_data" note replaces candles and MAE/MFE.
    """
    return await _call("trade", trade_id, timeframe, bars_before, bars_after, max_bars, include_candles)


@server.tool(annotations=READ_ONLY)
async def get_candles(symbol: str, timeframe: str = "H1", date_from: str | None = None, date_to: str | None = None,
                      count: int | None = None, max_bars: int = 500) -> dict[str, Any]:
    """OHLCV candles for any symbol and timeframe (M1..MN1). Without date_from returns the last `count`
    candles (default 200). Never returns more than max_bars rows.
    """
    return await _call("candles", symbol, timeframe, date_from, date_to, count, max_bars)


@server.tool(annotations=READ_ONLY)
async def get_live() -> dict[str, Any]:
    """Live state right now: open positions with floating P/L, balance, equity, margin level."""
    return await _call("live")


@server.tool(annotations=READ_ONLY)
async def get_symbol(symbol: str) -> dict[str, Any]:
    """Symbol specification (digits, point, tick value, contract size) and the latest bid/ask."""
    return await _call("symbol", symbol)


@server.tool(annotations=READ_ONLY)
async def trade_stats(date_from: str | None = None, date_to: str | None = None, group_by: str | None = None,
                      symbol: str | None = None) -> dict[str, Any]:
    """Journal statistics over closed trades: win rate, profit factor, expectancy, average win/loss,
    max drawdown, losing streak, best/worst trade net, costs.
    group_by: symbol, side, weekday, hour, day, month, exit_reason, magic. weekday and hour use the
    trade's open time, day and month its close time (broker server time).
    """
    return await _call("stats", date_from, date_to, group_by, symbol)


@server.tool(annotations=READ_ONLY)
async def chart_image(trade_id: str | None = None, symbol: str | None = None, timeframe: str | None = None,
                      date_from: str | None = None, date_to: str | None = None, count: int = 150,
                      show_volume: bool = False) -> Image:
    """PNG candlestick chart. With trade_id: the trade as a position box (entry→exit coloured by result),
    SL/TP zones, price tags and a header with net, exit reason, MAE/MFE. Otherwise a plain chart of
    `symbol` (last `count` candles, or date_from..date_to). show_volume adds a tick-volume panel.
    """
    png = await _call("chart", trade_id, symbol, timeframe, date_from, date_to, count, volume=show_volume)
    return Image(data=png, format="png")


def main(bridge_dir: str | None = None, timeout: float = 15.0) -> None:
    _state.update(bridge_dir=bridge_dir, timeout=timeout, bridge=None)
    server.run("stdio")


if __name__ == "__main__":
    main()
