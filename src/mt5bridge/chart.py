"""PNG candlestick charts for trades and symbols. Needs matplotlib (pip install 'mt5bridge[chart]').

Trade charts follow the "position tool" look traders know from TradingView: a box from entry to
exit coloured by the result, faint SL/TP zones, clean entry/exit dots, price tags on the axis,
and a header with the numbers a journal needs.
"""

import io
from datetime import datetime, timezone
from typing import Any

from .errors import BridgeError
from .timeframes import TF_SECONDS

T = {
    "bg": "#0b0f17",
    "grid": "#161c28",
    "text": "#e6edf3",
    "muted": "#7d8590",
    "chip": "#1b2230",
    "up": "#16c784",
    "down": "#ea3943",
    "buy": "#3b82f6",
    "sell": "#f59e0b",
    "win": "#16c784",
    "loss": "#ea3943",
}
FONTS = ("Inter", "Helvetica Neue", "Avenir Next", "Segoe UI", "Roboto", "Arial", "DejaVu Sans")
REASONS = {
    "sl": "Stop loss", "tp": "Take profit", "so": "Stop out", "client": "Manual",
    "mobile": "Manual · mobile", "web": "Manual · web", "expert": "Expert", "rollover": "Rollover",
    "vmargin": "Variation margin", "split": "Split",
}
DIM = 0.32          # opacity of candles outside the trade
ZONE = 0.09         # opacity of SL/TP zones
RESULT = 0.20       # opacity of the entry→exit box


def _font() -> str:
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    return next((f for f in FONTS if f in names), "DejaVu Sans")


def _fmt(price: float | None, digits: int | None) -> str:
    if price is None:
        return "-"
    return f"{price:.{digits}f}" if digits is not None else f"{price:g}"


def _bar_index(rows: list[list], ts: float | None) -> int | None:
    if ts is None or not rows:
        return None
    idx = None
    for i, r in enumerate(rows):
        if r[0] <= ts:
            idx = i
        else:
            break
    return idx


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _chips(fig, x: float, y: float, items: list[tuple]) -> None:
    """Lay out text runs left to right in figure coords. Item: (text, color, background|None, size, bold)."""
    renderer = fig.canvas.get_renderer()
    px = fig.bbox.width
    for text, color, bg, size, bold in items:
        kw = {}
        pad = 0.0
        if bg:
            kw["bbox"] = {"boxstyle": "round,pad=0.42,rounding_size=0.75", "fc": bg, "ec": "none"}
            pad = 0.42 * size * fig.dpi / 72
        t = fig.text(x + pad / px, y, text, color=color, fontsize=size, fontweight="bold" if bold else "normal",
                     ha="left", va="center", **kw)
        width = t.get_window_extent(renderer=renderer).width
        x += (width + 2 * pad + 0.55 * size * fig.dpi / 72) / px


def _price_axis(ax, lo: float, hi: float, tag_ys: list[float]) -> None:
    """Fixed price ticks with plain labels (no offset notation), blank where a tag covers them."""
    from matplotlib.ticker import MaxNLocator

    locs = [v for v in MaxNLocator(nbins=8, steps=[1, 2, 2.5, 5, 10]).tick_values(lo, hi) if lo <= v <= hi]
    if not locs:
        return
    step = locs[1] - locs[0] if len(locs) > 1 else abs(locs[0]) or 1.0
    decimals = next((d for d in range(11) if abs(round(step, d) - step) <= step * 1e-6), 10)
    gap = (hi - lo) * 0.05
    labels = ["" if any(abs(v - y) < gap * 0.85 for y in tag_ys) else f"{v:.{decimals}f}" for v in locs]
    ax.set_yticks(locs)
    ax.set_yticklabels(labels)


def _tags(ax, tags: list[tuple[float, str, str, str]], lo: float, hi: float) -> list[float]:
    """Price tags on the right axis: (price, text, background, foreground). Spread apart if they
    collide. Returns where they were drawn."""
    from matplotlib.transforms import blended_transform_factory

    if not tags:
        return []
    gap = (hi - lo) * 0.05
    placed = sorted(tags, key=lambda t: t[0])
    ys = [min(max(p, lo + gap / 2), hi - gap / 2) for p, *_ in placed]
    for i in range(1, len(ys)):                      # push up
        ys[i] = max(ys[i], ys[i - 1] + gap)
    for i in range(len(ys) - 2, -1, -1):             # and back down if we left the axis
        ys[i] = min(ys[i], ys[i + 1] - gap)
    tr = blended_transform_factory(ax.transAxes, ax.transData)
    for y, (_, text, bg, fg) in zip(ys, placed):
        ax.text(1.008, y, text, transform=tr, ha="left", va="center", fontsize=7.5, fontweight="bold", color=fg,
                bbox={"boxstyle": "round,pad=0.35,rounding_size=0.3", "fc": bg, "ec": "none"},
                clip_on=False, zorder=20)
    return ys


def _time_labels(rows: list[list], tf: str, ticks: list[int]) -> list[str]:
    sec = TF_SECONDS.get(tf, 3600)
    if sec >= 86400:
        pattern = "%d %b %Y" if rows[-1][0] - rows[0][0] > 300 * 86400 else "%d %b"
    elif rows[-1][0] - rows[0][0] < 86400:
        pattern = "%H:%M"
    else:
        pattern = "%d %b %H:%M"
    return [_dt(rows[i][0]).strftime(pattern) for i in ticks]


def render(rows: list[list], *, symbol: str, tf: str, trade: dict[str, Any] | None = None,
           digits: int | None = None, excursions: dict[str, Any] | None = None, volume: bool = False,
           width: float = 12.0, height: float = 6.75, dpi: int = 150) -> bytes:
    """Render raw EA rows [time, open, high, low, close, volume, spread] to PNG bytes.

    volume=True adds a tick-volume panel under the candles.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import to_rgba
        from matplotlib.patches import Rectangle
    except ImportError as exc:
        raise BridgeError("charts need matplotlib: pip install 'mt5bridge[chart]'") from exc
    if not rows:
        raise BridgeError("no candles to draw")

    n = len(rows)
    xs = list(range(n))
    opens, highs, lows, closes = ([r[k] for r in rows] for k in (1, 2, 3, 4))
    vols = [r[5] for r in rows]
    lo, hi = min(lows), max(highs)
    span = (hi - lo) or abs(hi) * 0.001 or 1.0

    i_in = i_out = None
    if trade:
        i_in = _bar_index(rows, trade.get("_open_ts"))
        i_out = _bar_index(rows, trade.get("_close_ts")) if trade.get("_close_ts") else n - 1

    def alpha(i: int) -> float:
        if trade is None or i_in is None:
            return 1.0
        return 1.0 if i_in <= i <= (i_out if i_out is not None else n - 1) else DIM

    up = [c >= o for o, c in zip(opens, closes)]
    colors = [to_rgba(T["up"] if u else T["down"], alpha(i)) for i, u in enumerate(up)]

    # DejaVu Sans as a per-glyph fallback (→, ▲, ▼ are missing from some system fonts).
    plt.rcParams.update({"font.family": [_font(), "DejaVu Sans"], "axes.unicode_minus": True})
    fig = plt.figure(figsize=(width, height), dpi=dpi, facecolor=T["bg"])
    box = {"left": 0.03, "right": 0.905, "top": 0.845, "bottom": 0.075}
    if volume:
        gs = fig.add_gridspec(2, 1, height_ratios=[5, 1], hspace=0.04, **box)
        ax = fig.add_subplot(gs[0])
        axv = fig.add_subplot(gs[1], sharex=ax)
    else:
        ax = fig.add_subplot(fig.add_gridspec(1, 1, **box)[0])
        axv = None
    bottom_ax = axv or ax
    for a in (ax, axv):
        if a is None:
            continue
        a.set_facecolor(T["bg"])
        for s in a.spines.values():
            s.set_visible(False)
        a.tick_params(colors=T["muted"], labelsize=8, length=0)

    # Candles (and volume)
    ax.vlines(xs, lows, highs, colors=colors, linewidth=1.0, zorder=3)
    bodies = [max(abs(c - o), span * 0.0016) for o, c in zip(opens, closes)]
    ax.bar(xs, bodies, bottom=[min(o, c) for o, c in zip(opens, closes)], color=colors, width=0.66,
           linewidth=0, zorder=4)
    if axv is not None:
        axv.bar(xs, vols, color=[to_rgba(T["up"] if u else T["down"], 0.32 * alpha(i)) for i, u in enumerate(up)],
                width=0.66, linewidth=0)
        axv.set_yticks([])
        axv.set_ylim(0, max(vols) * 1.15 if max(vols) else 1)

    y_lo, y_hi = lo - span * 0.06, hi + span * 0.06
    tags: list[tuple[float, str, str, str]] = []

    if trade:
        buy = trade["side"] == "buy"
        op = trade.get("open_price")
        closed = trade.get("_close_ts") is not None and trade.get("close_price") is not None
        cp = trade["close_price"] if closed else closes[-1]
        won = trade["net"] >= 0 if closed else ((cp - op) if buy else (op - cp)) >= 0
        result = T["win"] if won else T["loss"]
        x0 = (i_in if i_in is not None else 0) - 0.5
        x1 = (i_out if i_out is not None else n - 1) + 0.5

        def box(y0: float, y1: float, color: str, a: float) -> None:
            ax.add_patch(Rectangle((x0, min(y0, y1)), x1 - x0, abs(y1 - y0), facecolor=color, alpha=a,
                                   edgecolor="none", zorder=1))

        # SL / TP zones (levels set after entry count too). Far-away levels only get a pinned tag.
        for key, color, name in (("tp", T["win"], "TP"), ("sl", T["loss"], "SL")):
            level = trade.get(key) or trade.get(f"{key}_at_exit")
            if not level or op is None:
                continue
            visible = lo - span <= level <= hi + span
            if visible:
                box(op, level, color, ZONE)
                ax.hlines(level, x0, x1, colors=color, linewidth=1.0, alpha=0.7, zorder=2)
                ax.hlines(level, x1, n - 0.5, colors=color, linewidth=0.8, alpha=0.3, linestyles=(0, (1, 3)), zorder=2)
                y_lo, y_hi = min(y_lo, level - span * 0.03), max(y_hi, level + span * 0.03)
                tags.append((level, f"{name}  {_fmt(level, digits)}", color, T["bg"]))
            else:
                arrow = "▲" if level > hi else "▼"
                tags.append((level, f"{arrow} {name}  {_fmt(level, digits)}", T["chip"], color))

        if op is not None:
            box(op, cp, result, RESULT)
            ax.hlines(op, x0, x1, colors=T["text"], linewidth=1.1, alpha=0.75, zorder=2)
            ax.hlines(op, x1, n - 0.5, colors=T["text"], linewidth=0.8, alpha=0.25, linestyles=(0, (1, 3)), zorder=2)
            ax.hlines(cp, x0, x1, colors=result, linewidth=1.1, alpha=0.9, zorder=2)
            ax.hlines(cp, x1, n - 0.5, colors=result, linewidth=0.8, alpha=0.3, linestyles=(0, (1, 3)), zorder=2)
            if i_in is not None:
                ax.scatter([i_in], [op], s=58, color=T["buy"] if buy else T["sell"], edgecolors="white",
                           linewidths=1.6, zorder=10)
            if closed and i_out is not None:
                ax.scatter([i_out], [cp], s=58, color=result, edgecolors="white", linewidths=1.6, zorder=10)
            tags.append((op, f"ENTRY  {_fmt(op, digits)}", T["buy"] if buy else T["sell"], "white"))
            tags.append((cp, f"{'EXIT' if closed else 'NOW'}  {_fmt(cp, digits)}", result, "white"))

        # Header
        side_color = T["buy"] if buy else T["sell"]
        chips = [
            (symbol, T["text"], None, 16, True),
            (tf, T["muted"], None, 11, False),
            (f"{trade['side'].upper()} {trade['volume']:g}", "white", side_color, 9, True),
        ]
        if closed:
            reason = trade.get("exit_reason") or ""
            chips += [(f"{trade['net']:+,.2f}", "white", result, 9, True),
                      (REASONS.get(reason, reason.title() or "Closed"), T["text"], T["chip"], 9, False)]
        else:
            chips.append(("Open", T["text"], T["chip"], 9, False))
        _chips(fig, 0.03, 0.945, chips)
        when = _dt(trade["_open_ts"]).strftime("%d %b %H:%M")
        if closed:
            when += "  →  " + _dt(trade["_close_ts"]).strftime("%d %b %H:%M")
        details = [when]
        if trade.get("duration"):
            details.append(trade["duration"])
        ex = excursions if excursions and "error" not in excursions else {}
        if ex.get("mae_points") is not None:
            details.append(f"MAE {ex['mae_points']:g} pts")
        if ex.get("mfe_points") is not None:
            details.append(f"MFE {ex['mfe_points']:g} pts")
        if ex.get("r_multiple") is not None:
            details.append(f"R {ex['r_multiple']:+.2f}")
        details.append(f"#{trade['id']}")
        _chips(fig, 0.03, 0.892, [("   ·   ".join(details), T["muted"], None, 9, False)])
    else:
        change = (closes[-1] - opens[0]) / opens[0] * 100 if opens[0] else 0.0
        _chips(fig, 0.03, 0.945, [
            (symbol, T["text"], None, 16, True),
            (tf, T["muted"], None, 11, False),
            (_fmt(closes[-1], digits), T["text"], None, 11, True),
            (f"{change:+.2f}%", "white", T["win"] if change >= 0 else T["loss"], 9, True),
        ])
        _chips(fig, 0.03, 0.892, [(
            f"{_dt(rows[0][0]).strftime('%d %b %H:%M')}  →  {_dt(rows[-1][0]).strftime('%d %b %H:%M')}"
            f"   ·   {n} bars   ·   H {_fmt(hi, digits)}   ·   L {_fmt(lo, digits)}", T["muted"], None, 9, False)])
        tags.append((closes[-1], _fmt(closes[-1], digits), T["up"] if closes[-1] >= opens[-1] else T["down"], "white"))

    # Axes
    ax.set_xlim(-1, n)
    ax.set_ylim(y_lo, y_hi)
    ax.yaxis.tick_right()
    ax.grid(axis="y", color=T["grid"], linewidth=0.8)
    ax.grid(axis="x", color=T["grid"], linewidth=0.5, alpha=0.6)
    ticks = list(range(0, n, max(1, n // 7)))
    bottom_ax.set_xticks(ticks)
    bottom_ax.set_xticklabels(_time_labels(rows, tf, ticks))
    if axv is not None:
        axv.grid(axis="x", color=T["grid"], linewidth=0.5, alpha=0.6)
        plt.setp(ax.get_xticklabels(), visible=False)
    _price_axis(ax, y_lo, y_hi, _tags(ax, tags, y_lo, y_hi))
    fig.text(0.905, 0.022, "mt5bridge", color=T["muted"], alpha=0.55, fontsize=7.5, ha="right", va="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=T["bg"])
    plt.close(fig)
    return buf.getvalue()
