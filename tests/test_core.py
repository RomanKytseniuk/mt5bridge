import pytest

from mt5bridge.testing import DEALS, NOW, ts
from mt5bridge.timeframes import auto_tf, normalize_tf
from mt5bridge.timeutil import parse_time
from mt5bridge.trades import build_trades


def test_normalize_tf():
    assert normalize_tf("h1") == "H1"
    assert normalize_tf("1h") == "H1"
    assert normalize_tf("60m") == "H1"
    assert normalize_tf("15m") == "M15"
    assert normalize_tf("1d") == "D1"
    assert normalize_tf("MN") == "MN1"
    with pytest.raises(ValueError):
        normalize_tf("H5")


def test_auto_tf():
    assert auto_tf(3600, 60) == "M1"
    assert auto_tf(4 * 3600, 60) == "M5"
    assert auto_tf(3 * 86400, 60) == "H4"


def test_parse_time():
    assert parse_time("2026-11-10", NOW) == ts("2026-11-10 00:00")
    assert parse_time("2026-11-10", NOW, end_of_day=True) == ts("2026-11-10 00:00") + 86399
    assert parse_time("2026-11-10 14:30", NOW) == ts("2026-11-10 14:30")
    assert parse_time("7d", NOW) == NOW - 7 * 86400
    assert parse_time("now", NOW) == NOW
    assert parse_time(None, NOW) is None
    with pytest.raises(ValueError):
        parse_time("last tuesday", NOW)


def _by_id(trades):
    return {t["id"]: t for t in trades}


def test_build_trades_groups_deals():
    trades, cash = build_trades(DEALS)
    t = _by_id(trades)
    assert cash[0]["type"] == "balance" and cash[0]["amount"] == 10000.0

    tp = t["1001"]
    assert tp["side"] == "buy" and tp["status"] == "closed" and tp["exit_reason"] == "tp"
    assert tp["open_price"] == 1.07 and tp["close_price"] == 1.075
    assert tp["sl"] == 1.068 and tp["tp"] == 1.075
    assert tp["net"] == pytest.approx(48.6)
    assert tp["duration"] == "4h"

    partial = t["1002"]
    assert partial["side"] == "sell" and partial["volume"] == 0.2 and partial["exits"] == 2
    assert partial["close_price"] == pytest.approx(2650.0)  # VWAP of 2645 and 2655
    assert partial["exit_reason"] == "sl"
    assert partial["net"] == pytest.approx(-2.0)


def test_reversal_splits_into_two_trades():
    trades, _ = build_trades([d for d in DEALS if d["position"] == 1004])
    first, second = trades
    assert (first["id"], first["side"], first["volume"]) == ("1004", "buy", 0.1)
    assert first["close_price"] == 1.11 and first["profit"] == 100.0
    assert (second["id"], second["side"], second["volume"]) == ("1004-2", "sell", 0.2)
    assert second["open_price"] == 1.11 and second["close_price"] == 1.105
    # commission of the reversal deal is split by volume: 1/3 to the closing part, 2/3 to the new one
    assert first["commission"] == pytest.approx(-0.3)
    assert second["commission"] == pytest.approx(-0.6)


def test_open_position_is_marked_open():
    trades, _ = build_trades([d for d in DEALS if d["position"] == 1005])
    assert trades[0]["status"] == "open" and trades[0]["close_time"] is None


def test_exit_without_entry_is_incomplete():
    trades, _ = build_trades([d for d in DEALS if d["ticket"] == 31])
    assert trades[0]["status"] == "incomplete" and trades[0]["side"] == "buy"


def test_several_terminals_stay_on_one(tmp_path, monkeypatch):
    import os
    import time

    from mt5bridge import discovery

    dirs = []
    for name in ("b-terminal", "a-terminal"):
        mql5 = tmp_path / name / "MQL5"
        (mql5 / "Files" / "mt5bridge").mkdir(parents=True)
        dirs.append(mql5)
    monkeypatch.setattr(discovery, "find_mql5_dirs", lambda: dirs)
    monkeypatch.delenv(discovery.ENV_DIR, raising=False)
    beat = {d: d / "Files" / "mt5bridge" / "status.json" for d in dirs}

    for d in dirs:  # both EAs running: always the same one, whichever wrote last
        beat[d].write_text("{}")
    assert discovery.resolve_bridge_dir().parent.parent.parent.name == "a-terminal"
    beat[dirs[0]].write_text("{}")
    assert discovery.resolve_bridge_dir().parent.parent.parent.name == "a-terminal"

    old = time.time() - 600  # only b runs now
    os.utime(beat[dirs[1]], (old, old))
    assert discovery.resolve_bridge_dir().parent.parent.parent.name == "b-terminal"
