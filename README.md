# mt5bridge

**Read-only MetaTrader 5 bridge for humans and AI agents.** Your trade journal, candles on any
timeframe, MAE/MFE per trade and live positions, as JSON, from a command-line utility, with an
MCP server on top so Claude (or any MCP client) can read your MT5.

- **Works on macOS** (the official MT5 app runs under Wine), Windows and Linux. No
  Windows-only `MetaTrader5` Python package.
- **Read-only by design.** Nothing in it can open, modify or close an order.
- **Journal, not just data:** deals are grouped into trades (partial closes, reversals), and
  every trade gets MAE/MFE, efficiency and R-multiples computed from the candles.
- **One core, two faces:** `mt5bridge` CLI prints JSON; `mt5bridge mcp` serves the same
  functions as MCP tools.

```
Claude / any MCP client ──stdio──▶ mt5bridge mcp ─┐
you / scripts / Claude via shell ──▶ mt5bridge CLI ─┤  Python core
                                                    ▼
                         MQL5/Files/mt5bridge  (request/response files)
                                                    ▲
                         MT5Bridge EA on any chart in MetaTrader 5
```

## Install

Requires Python 3.10+ and MetaTrader 5.

```bash
git clone <this repo> mt5bridge && cd mt5bridge
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"      # [mcp] and [chart] are separate extras too
```

## Install with an AI agent

Give your agent (Claude Code, OpenCode, ...) this repository and say *"install mt5bridge"*.
These steps are written for it. Do every step yourself; give the user step-by-step instructions
only for a step that failed or that needs the MT5 window.

1. Clone the repository and install it into its own virtual environment, as in [Install](#install).
2. Run `.venv/bin/mt5bridge setup` and read its JSON:
   - `ea[].compiled` false: show the user `log_tail`, ask them to open `MQL5/Experts/MT5Bridge.mq5`
     in MetaEditor (in MT5: **Tools → MetaQuotes Language Editor**) and press **F7**.
   - `mt5.attached` false: give the user `mt5.reason` and `mt5.user_steps`, wait for their reply.
3. Run `mt5bridge status`: it must show `"ea_running": true` and `"next": null`. If `next` is set,
   it says what is missing; do that and check again.
4. Register the MCP server with your client (see [MCP server](#mcp-server)), then tell the user to
   start a new session.

## Set up MetaTrader 5

```bash
.venv/bin/mt5bridge setup
```

One command, safe to run again at any time:

1. Copies `MT5Bridge.mq5` into every MT5 terminal it finds and compiles it with MetaEditor (on
   macOS through MT5's own Wine). Skipped when the installed EA is up to date.
2. Installs the [agent skill](#agent-skill) and links `mt5bridge` into `~/.local/bin` (when that
   folder is on PATH and no other `mt5bridge` is), so agents find both from any folder.
3. Starts MT5 with the EA on a chart, if MT5 is closed. MT5 cannot attach an expert from outside
   while it runs, but it can at start: when your last profile already has the EA on a chart, MT5
   just starts; otherwise it starts with a one-time `[StartUp]` config that opens a chart with the
   EA. If MT5 is open and the EA is not on a chart, `setup` returns the steps to do it by hand:
   **View → Navigator → Expert Advisors → MT5Bridge**, drag it onto any chart, press **OK**.

`--no-start`, `--no-skill`, `--skills-dir <folder>` and `--mql5 <folder>` narrow it down.

Tips:
- The bridge never trades, so the **Algo Trading** button can stay off.
- Keep the EA's chart in your profile: MT5 brings it back on every start.
- **After updating mt5bridge**, close MT5 and run `mt5bridge setup`: it compiles the new EA and
  starts MT5 with it (a running MT5 does not reload an expert compiled outside the terminal).
- Manual install: copy `src/mt5bridge/mql5/MT5Bridge.mq5` to `<MT5 data folder>/MQL5/Experts/`
  and press F7 in MetaEditor.

## CLI

Every command prints JSON (indented in a terminal, one line when piped; force with
`--pretty` / `--compact`). Errors are JSON too: `{"ok": false, "error": "..."}` with exit code 1.

```bash
mt5bridge status                                  # is MT5 + EA alive, server time, UTC offset
mt5bridge account                                 # balance, equity, margin, demo/real
mt5bridge trades --from 7d                        # closed trades, newest first
mt5bridge trades --from 2026-10-01 --symbol XAUUSD --side sell
mt5bridge trade 123456789                         # one trade + MAE/MFE + candles around it
mt5bridge trade 123456789 --tf M15 --before 50
mt5bridge candles EURUSD H1 --count 100           # any timeframe M1..MN1
mt5bridge candles XAUUSD M5 --from "2026-11-14 08:00" --to "2026-11-14 12:00"
mt5bridge stats --from 2026-10-01 --by weekday    # win rate, PF, expectancy, drawdown...
mt5bridge chart 123456789 -o trade.png            # PNG: position box, SL/TP zones, MAE/MFE header
mt5bridge chart --symbol XAUUSD --tf H1 --volume  # symbol chart; --volume adds a tick-volume panel
mt5bridge live                                    # open positions right now
mt5bridge watch                                   # live table, refreshes every 2s
mt5bridge watch --json                            # one JSON line per refresh, for scripts
mt5bridge deals --from 1d                         # raw MT5 deals
mt5bridge setup                                   # install/update EA + agent skill, start MT5
```

Times: `YYYY-MM-DD`, `"YYYY-MM-DD HH:MM"`, `now`, `today`, `yesterday`, or look-backs `12h`,
`7d`, `2w`. Timeframes: `M1 M5 M15 M30 H1 H4 D1 W1 MN1` (plus M2..M20, H2..H12; `1h`, `15m`
work too).

### What a trade looks like

```json
{
  "trade": {
    "id": "123456789", "symbol": "EURUSD", "side": "buy", "volume": 0.1,
    "open_time": "2026-11-10 10:00:00", "open_price": 1.07,
    "close_time": "2026-11-10 14:00:00", "close_price": 1.075,
    "sl": 1.068, "tp": 1.075, "sl_at_exit": 1.068, "tp_at_exit": 1.075,
    "exit_reason": "tp", "exits": 1,
    "profit": 50.0, "commission": -1.4, "swap": 0.0, "fee": 0.0, "net": 48.6,
    "duration": "4h", "status": "closed", "magic": 0, "comment": ""
  },
  "excursions": {
    "tf": "M1",
    "mae_points": 100.0, "mae_money": 10.0,
    "mfe_points": 600.0, "mfe_money": 60.0,
    "captured_points": 500.0, "gave_back_points": 100.0, "efficiency": 0.83,
    "risk_points": 200.0, "r_multiple": 2.5, "mae_r": 0.5, "mfe_r": 3.0
  },
  "candles": {
    "symbol": "EURUSD", "tf": "M5", "count": 94, "truncated": false,
    "cols": ["time", "open", "high", "low", "close", "volume"],
    "rows": [["2026-11-10 07:30", 1.07, 1.07, 1.07, 1.07, 100], "..."],
    "entry_index": 30, "exit_index": 78
  }
}
```

- **MAE** (max adverse excursion): how far price went against the trade. **MFE**: how far it
  went in your favour. **Gave back** = MFE − captured: how far price came back from the best
  point before the exit. **Efficiency** = captured / MFE, winners only. R values use the SL at
  entry.
- Old trades on symbols the broker has removed still come back, with a `market_data` note
  instead of candles.
- Symbols are forgiving: `eurusd` finds `EURUSD`, and `EURUSD` finds the broker's `EURUSD.s`
  or `EURUSD+` when only one of them is in Market Watch.
- Candles are columns + rows to keep token use low. The timeframe is picked from the trade's
  duration unless you pass one.

## MCP server

```bash
mt5bridge mcp            # stdio; same as: mt5bridge-mcp
```

**Claude Code:**

```bash
claude mcp add mt5bridge -s user -- /absolute/path/to/.venv/bin/mt5bridge mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "mt5bridge": {
      "command": "/absolute/path/to/.venv/bin/mt5bridge",
      "args": ["mcp"]
    }
  }
}
```

| Tool | What it returns |
|---|---|
| `status` | MT5 + EA alive, terminal build, server time, UTC offset |
| `account` | balance, equity, margin, leverage, demo/real, netting/hedging |
| `list_trades` | closed trades with exit reason, SL/TP, costs, net |
| `get_trade` | one trade + MAE/MFE/R + candles with entry/exit indexes |
| `get_candles` | OHLCV on any timeframe, capped by `max_bars` |
| `get_live` | open positions with floating P/L, account snapshot |
| `get_symbol` | symbol spec and the latest bid/ask |
| `trade_stats` | win rate, profit factor, expectancy, drawdown, grouped by symbol/weekday/hour/... |
| `chart_image` | PNG chart of a trade (entry/exit, SL/TP) or of a symbol; `show_volume` adds volume |

Every tool is marked read-only. Ask things like *"why do I lose on Fridays?"*,
*"show my worst XAUUSD trade this month and what price did after I exited"*, or
*"how much profit am I leaving on the table on average (MFE vs captured)?"*

**Claude without MCP:** the CLI is enough. Claude Code can run `mt5bridge trades --from 7d`
in a shell and read the JSON directly.

## Agent skill

The MCP server sends every client short instructions with its tools. The
[skill](src/mt5bridge/skills/mt5bridge/SKILL.md) goes further: which calls answer which question,
how to read MAE/MFE, efficiency and R, what to do when MT5 or the EA is not reachable, and the CLI
fallback when MCP is not connected.

`mt5bridge setup` installs it into `~/.claude/skills` (Claude Code and OpenCode read it there), so
there is no extra step. Agents only load skills from their skills folder, which is why it ships
inside the package instead of a `.agents/skills` folder in this repository: that one is read only
by an agent working inside this repository. Other agents: `mt5bridge setup --skills-dir <folder>`.
Claude Desktop or claude.ai: upload `src/mt5bridge/skills/mt5bridge` as a ZIP in the app's
skills settings.

## Notes

- **Times are broker server time**, exactly as on the MT5 chart. `status` shows the current
  UTC offset.
- MAE/MFE are approximate: they come from bar highs/lows (bid prices) on the finest timeframe
  that fits, and the entry/exit bars count whole.
- Asking for a symbol that is not in Market Watch adds it there (MT5 needs that to load history).
  The first request for old history can take a few seconds while MT5 downloads it.
- Several terminals? `--dir <terminal>/MQL5/Files` or `MT5BRIDGE_DIR` picks one; by default the
  first terminal (by folder path) with a running EA wins, so one session stays on one account.

## How it works

The EA polls `MQL5/Files/mt5bridge/req/` every 100 ms. A client writes `<id>.req` (UTF-8
`key=value` lines, first `cmd=...`), the EA answers with `resp/<id>.json`
(`{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`) and rewrites `status.json`
every second as a heartbeat. Both sides write a temp file and rename it, so nobody reads half a
file, and any number of clients can share one EA. Files instead of sockets: nothing to allow in
MT5 settings, and it behaves the same under Wine.

EA commands: `ping`, `account`, `deals`, `position_deals`, `rates`, `positions`, `symbol`.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Tests run against `tests/fake_ea.py`, a stand-in EA that speaks the same file protocol, so
they need no MetaTrader.

## Disclaimer

Not financial advice. mt5bridge reads data; decisions and their results are yours.

## License

MIT
