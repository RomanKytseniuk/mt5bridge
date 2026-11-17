---
name: mt5bridge
description: Answers questions about the user's live MetaTrader 5 account through mt5bridge (MCP tools or CLI) - closed trades, journal statistics (win rate, profit factor, drawdown by day, hour, symbol or EA magic), MAE/MFE, R-multiples, price before entry or after exit, open positions, balance and margin, trade charts - and installs or reconnects the bridge when MT5 or its EA is unreachable. Use when the user asks about their MT5/MetaTrader 5 trades, journal, P/L, losses, drawdown, an MT5 position ID, a chart of one of their trades, or to install, connect or check mt5bridge. Don't use for MT4, cTrader, crypto exchanges or exported statement files; placing, changing or closing orders; writing MQL5 indicators or EAs; forecasts, signals or strategy advice; or editing mt5bridge's source code.
---

# MT5 journal via mt5bridge

Answers questions about the user's MT5 account with numbers taken only from mt5bridge. Done when every number in the answer comes from a tool result of this session; for a setup request, done when `status` returns `ea_running: true` and `next: null`.

## Procedure

**Step 1: Pick the interface**
1. If the agent can run no commands on the computer where MT5 runs and has no `mt5bridge` MCP tools (for example a cloud chat), install nothing: point the user to the "Install with an AI agent" section of the mt5bridge README and stop.
2. If the tools of the `mt5bridge` MCP server are available (`status`, `list_trades`, `get_trade`, ...), use them for every data call below.
3. If the agent can run commands, find the executable; the CLI and the shell commands of Step 2 need it. Run `mt5bridge --version`. If it is not found, take the executable path from the `mt5bridge` entry of the agent's MCP configuration (in Claude Code: `claude mcp get mt5bridge`) and use that path in place of `mt5bridge`.
4. If neither MCP tools nor an executable were found, ask the user for the executable path or the mt5bridge checkout or repository. If `<path> --version` works, use that path. Otherwise read the "Install with an AI agent" section of the mt5bridge README and follow it.
5. Without MCP tools, use the CLI. Run `mt5bridge --help` once and match the tool names below to commands by their descriptions (`list_trades` is `trades`, `trade_stats` is `stats`, `get_trade` is `trade`, `chart_image` is `chart`, ...); it also holds the field notes for Step 4. Run `mt5bridge <command> --help` before using a flag for the first time. Put global flags (`--compact`, `--dir`) before the command.

**Step 2: Check the connection**
1. Call `status`.
2. If `ea_running` is true, note `bridge_dir` and the account that `status` names. With the CLI, pass `--dir <bridge_dir>` on every later call so that all numbers come from one account. If `status` has an `error` or reports the terminal as not connected, warn that live data may be stale. Keep any `next` text for the end of the answer and go to Step 3. For a setup request: if `next` is null, report success and stop; otherwise handle `next` as in item 3.
3. If `ea_running` is false, run the shell command the `next` text names (usually `mt5bridge setup`) with the executable from Step 1, and read its result:
   - An `ea` entry with `compiled: false`: show the user its `reason` or `log_tail` and ask them to compile `MT5Bridge.mq5` in MetaEditor (F7).
   - `mt5.attached` false: give the user `mt5.reason` and `mt5.user_steps` in their language and wait for the reply.
   - If `next` names no command, give its text to the user in their language and wait for the reply.
   Then call `status` again. Never close MT5 yourself: ask the user to.
4. If `ea_running` stays false after the user attached the EA, check that `bridge_dir` belongs to the terminal they used. If not: with the CLI, pass `--dir`; with MCP, ask the user to re-register the server with `--dir` and restart the agent.
5. Stop after 3 failed `status` checks and show the user the last `status` result.

**Step 3: Fetch the data**
1. Turn the user's period into `date_from`/`date_to` using the formats in the server instructions or `mt5bridge --help`. If the question needs a period and the user gave none, use the tool default and name the period in the answer.
2. Keep times in the zone the tools use. Convert to local time only when the user asks and their time zone is known; if the period crosses a daylight-saving change, say that converted hours may be off by one.
3. Pick the calls by question type:
   - Overall results: `trade_stats`, then `list_trades` for the same period.
   - Which day, hour, symbol, side or exit reason loses: `trade_stats` grouped by that dimension.
   - Why one group loses: `trade_stats` for that grouping first, then `list_trades` for the period with all trades (see its `limit` description), keep the trades of that group (the `trade_stats` description says which time each grouping uses), and compare them with the other trades by exit reason, symbol and duration. Inspect the 3 worst with `get_trade`.
   - One trade by id: `get_trade`. Best or worst trades: `list_trades` with all trades, rank by `net`, and check the result against `best`/`worst` from `trade_stats`.
   - Price before the entry or after the exit: `get_trade` with more bars before or after; for a longer view, `get_candles` from the trade's open or close time. Name the window in the answer.
   - Open positions, floating P/L, margin: `get_live`. Balance, leverage, account type: `account`.
   - A picture of a trade or a symbol: `chart_image`; add the volume panel only when the user asks for volume. With the CLI, give the user the PNG path that the `chart` result names.
4. Before ranking or counting trades, check that `list_trades` returned all of them (`returned` equals `total`); if not, call it again asking for all trades.
5. Call `get_trade` without candles unless the price path matters. If the question needs more than 10 `get_trade` calls, answer from `trade_stats` and `list_trades` first and offer to inspect specific trades.

**Step 4: Read the numbers**
1. Before interpreting `net`, MAE/MFE, efficiency, R values or a `market_data` note, read the field notes in the server instructions and tool descriptions (CLI: `mt5bridge --help`).
2. Judge results by `net`, not `profit`, unless the user asks for gross.
3. Present MAE/MFE as approximate, rounded values.
4. Treat a missing efficiency or R value as "not applicable", never as zero, and say how many trades an average leaves out.
5. With fewer than 20 trades in a group, call a difference "too few trades to conclude" instead of ranking the groups.
6. If a trade has a `market_data` note, report the trade from its record and pass the note on.

**Step 5: Answer**
1. Lead with the direct answer in one or two sentences, then the supporting numbers.
2. Take every number from a tool result of this session. Name the period, the account currency (from `account` if no result in hand shows it) and the time zone of the times as the tool docs state it.
3. Cite the trade id of every individual trade mentioned.
4. Keep facts and interpretation apart: label guesses as possible reasons.
5. Describe what happened. Never recommend entries, position sizes or strategies.
6. Answer in the user's language. If Step 2 kept a `next` text, add it as the last line.

## Error Handling
- Any call says the EA is not running: go back to Step 2.
- "No MetaTrader 5 data folder found": ask the user for the MT5 data folder (in MT5: File -> Open Data Folder) and pass it the way the error message says.
- "unknown symbol" or "ambiguous symbol": retry with the exact broker name from the error's candidate list or from `list_trades`.
- "trade ... not found": search `list_trades` over a wider period and retry with the id it shows.
- Any other error: do what its message says, retry once, then show the message to the user.
- The user asks to open, change or close an order: say that mt5bridge is read-only and the user has to do it in MT5.
- A tool or command named here does not exist: list the server's tools or run `mt5bridge --help`, use the one with the same purpose, and tell the user this skill needs an update.
