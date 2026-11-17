import json
import time
from pathlib import Path

import pytest

from mt5bridge import Bridge, BridgeError, EANotRunning, RemoteError
from mt5bridge.cli import main as cli_main


def test_status(bridge):
    s = bridge.status()
    assert s["ea_running"] is True
    assert s["terminal"]["utc_offset"] == "+02:00"
    assert s["terminal"]["server_time"] == "2026-11-16 12:00:00"


def test_ea_not_running(bridge_dir):
    b = Bridge(bridge_dir=str(bridge_dir), timeout=1)
    t0 = time.monotonic()
    with pytest.raises(EANotRunning):
        b.account()
    assert time.monotonic() - t0 < 0.5  # fails fast, does not wait for the timeout


def test_trades_fills_positions_opened_before_range(bridge):
    res = bridge.trades(date_from="2026-11-05")
    ids = [t["id"] for t in res["trades"]]
    assert ids == ["1004-2", "1004", "1003", "1002", "1001"]  # newest first, open 1005 left out
    gbp = next(t for t in res["trades"] if t["id"] == "1003")
    assert gbp["status"] == "closed" and gbp["open_time"] == "2026-10-01 08:00:00"
    assert all(not k.startswith("_") for t in res["trades"] for k in t)


def test_trades_filters(bridge):
    assert [t["id"] for t in bridge.trades("2026-11-05", symbol="xauusd")["trades"]] == ["1002"]
    assert {t["side"] for t in bridge.trades("2026-11-05", side="sell")["trades"]} == {"sell"}
    with_open = bridge.trades("2026-11-05", include_open=True)["trades"]
    assert any(t["id"] == "1005" and t["status"] == "open" for t in with_open)
    assert bridge.trades("2026-11-05", limit=2)["returned"] == 2


def test_trade_excursions_and_candles(bridge):
    res = bridge.trade("1001")
    ex = res["excursions"]
    assert ex["tf"] == "M1"
    assert ex["mae_points"] == pytest.approx(100.0)   # 1.0700 -> low 1.0690
    assert ex["mfe_points"] == pytest.approx(600.0)   # 1.0700 -> high 1.0760
    assert ex["mae_money"] == pytest.approx(10.0)     # 10 pips on 0.1 lot EURUSD
    assert ex["captured_points"] == pytest.approx(500.0)
    assert ex["gave_back_points"] == pytest.approx(100.0)  # high 1.0760 -> exit 1.0750
    assert ex["efficiency"] == pytest.approx(0.83)
    assert ex["r_multiple"] == pytest.approx(2.5)
    assert ex["mae_r"] == pytest.approx(0.5) and ex["mfe_r"] == pytest.approx(3.0)

    c = res["candles"]
    assert c["tf"] == "M5"  # 4h trade, ~48 bars
    assert c["cols"] == ["time", "open", "high", "low", "close", "volume"]
    assert c["rows"][c["entry_index"]][0] == "2026-11-10 10:00"
    assert c["rows"][c["exit_index"]][0] == "2026-11-10 14:00"
    assert c["entry_index"] == 30 and len(c["rows"]) - 1 - c["exit_index"] == 15


def test_trade_with_explicit_tf_and_cap(bridge):
    res = bridge.trade("1001", timeframe="M1", max_bars=100)
    assert res["candles"]["tf"] == "M1"
    assert res["candles"]["count"] == 100 and res["candles"]["truncated"] is True
    assert "warning" in res["candles"]


def test_open_trade_uses_current_price(bridge):
    res = bridge.trade("1005", include_candles=False)
    assert res["trade"]["status"] == "open"
    assert res["excursions"]["captured_points"] == pytest.approx(200.0)  # 1.0800 -> bid 1.0820
    assert "candles" not in res


def test_losing_trade_has_no_efficiency(bridge):
    # 1002: exits average out at the entry price, net is negative after costs
    ex = bridge.trade("1002", include_candles=False)["excursions"]
    assert ex["captured_points"] == 0.0 and ex["efficiency"] is None


def test_delisted_symbol_trade_still_returned(bridge):
    res = bridge.trade("1006")
    assert res["trade"]["symbol"] == "GBPCHF+" and res["trade"]["net"] == 12.0
    assert res["excursions"] is None and "candles" not in res
    assert "removed this symbol" in res["market_data"]
    with pytest.raises(BridgeError, match="no market data"):
        bridge.chart(trade_id="1006")


def test_symbol_lookup_is_forgiving(bridge):
    assert bridge.candles("eurusd", "H1", count=3)["symbol"] == "EURUSD"
    assert [t["id"] for t in bridge.trades("2026-11-05", symbol="xauusd")["trades"]] == ["1002"]


def test_symbol_matches():
    from mt5bridge.service import symbol_matches

    assert symbol_matches("EURUSD.s", "eurusd")
    assert symbol_matches("EURUSD+", "EURUSD")
    assert symbol_matches("EURUSD.s", "EURUSD.S")
    assert not symbol_matches("EURUSDX", "EURUSD")
    assert not symbol_matches("EURUSD", "EURUSD.s")


def test_unknown_trade(bridge):
    with pytest.raises(BridgeError):
        bridge.trade("999999")
    with pytest.raises(BridgeError):
        bridge.trade("abc")


def test_candles(bridge):
    res = bridge.candles("EURUSD", "1h", count=24)
    assert res["tf"] == "H1" and res["count"] == 24
    assert res["rows"][-1][0] == "2026-11-16 12:00"
    ranged = bridge.candles("EURUSD", "H4", date_from="2026-11-10", date_to="2026-11-11")
    assert ranged["rows"][0][0] == "2026-11-10 00:00" and ranged["rows"][-1][0] == "2026-11-11 20:00"
    capped = bridge.candles("EURUSD", "M1", date_from="2026-11-01", max_bars=50)
    assert capped["count"] == 50 and capped["truncated"] and "warning" in capped
    with pytest.raises(RemoteError):
        bridge.candles("NOPE", "H1")


def test_live(bridge):
    res = bridge.live()
    assert res["account"]["trade_mode"] == "demo"
    assert res["floating"] == pytest.approx(9.8)
    assert res["positions"][0]["id"] == "1005" and res["positions"][0]["age"] == "3h"


def test_stats(bridge):
    res = bridge.stats(date_from="2026-11-05", by="symbol")
    o = res["overall"]
    assert o["trades"] == 5 and o["wins"] == 4 and o["losses"] == 1
    assert {g["symbol"] for g in res["groups"]} == {"EURUSD", "GBPUSD", "XAUUSD"}
    weekdays = bridge.stats(date_from="2026-11-05", by="weekday")["groups"]
    assert [g["weekday"] for g in weekdays] == ["Tue", "Wed", "Thu", "Fri"]


def test_chart_png(bridge):
    assert bridge.chart(trade_id="1002").startswith(b"\x89PNG")
    assert bridge.chart(trade_id="1005").startswith(b"\x89PNG")  # open: entry marker only
    assert bridge.chart(symbol="XAUUSD", timeframe="M15", count=40).startswith(b"\x89PNG")


def test_chart_volume_is_opt_in(bridge):
    plain = bridge.chart(trade_id="1002")
    with_volume = bridge.chart(trade_id="1002", volume=True)
    assert with_volume.startswith(b"\x89PNG") and with_volume != plain
    sym = bridge.chart(symbol="XAUUSD", timeframe="M15", count=40, volume=True)
    assert sym.startswith(b"\x89PNG")


def test_concurrent_clients(bridge_dir, fake_ea):
    a = Bridge(bridge_dir=str(bridge_dir), timeout=5)
    b = Bridge(bridge_dir=str(bridge_dir), timeout=5)
    assert a.account()["login"] == b.account()["login"] == 42


def test_cli_json(bridge_dir, fake_ea, capsys):
    assert cli_main(["--dir", str(bridge_dir), "--compact", "trades", "--from", "2026-11-05", "--symbol", "XAUUSD"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trades"][0]["id"] == "1002"

    assert cli_main(["--dir", str(bridge_dir), "candles", "EURUSD", "H7"]) == 1
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False and "timeframe" in err["error"]


def test_cli_chart(bridge_dir, fake_ea, tmp_path, capsys):
    out_file = tmp_path / "t.png"
    assert cli_main(["--dir", str(bridge_dir), "chart", "1001", "-o", str(out_file)]) == 0
    assert out_file.read_bytes().startswith(b"\x89PNG")
    vol_file = tmp_path / "v.png"
    assert cli_main(["--dir", str(bridge_dir), "chart", "1001", "--volume", "-o", str(vol_file)]) == 0
    assert vol_file.read_bytes() != out_file.read_bytes()


def test_status_next_step(bridge, bridge_dir):
    from mt5bridge.install import packaged_ea_version
    from mt5bridge.service import _next_step

    assert bridge.status()["next"] is None  # fake EA reports no version number: nothing to suggest
    idle = {"bridge_dir": str(bridge_dir), "ea_running": False, "heartbeat_age_s": None}
    assert "mt5bridge setup" in _next_step(idle)  # EA not installed
    experts = bridge_dir.parent.parent / "Experts"
    experts.mkdir()
    (experts / "MT5Bridge.ex5").write_bytes(b"")
    assert "Navigator" in _next_step(idle)  # installed, terminal unknown: both ways to start it
    shipped = packaged_ea_version()
    running = {"bridge_dir": str(bridge_dir), "ea_running": True, "heartbeat": {"ea_version": "0.9"}}
    assert shipped and shipped in _next_step(running) and "setup" in _next_step(running)
    assert _next_step({**running, "heartbeat": {"ea_version": shipped}}) is None


def _fake_terminal(tmp_path, with_ea: bool):
    data = tmp_path / "MetaTrader 5"
    (data / "MQL5" / "Experts").mkdir(parents=True)
    (data / "config").mkdir()
    (data / "config" / "common.ini").write_text("[Charts]\r\nProfileLast=Euro\r\n", encoding="utf-16")
    charts = data / "MQL5" / "Profiles" / "Charts" / "Euro"
    charts.mkdir(parents=True)
    expert = "<expert>\r\nname=MT5Bridge\r\npath=Experts\\MT5Bridge.ex5\r\n</expert>\r\n" if with_ea else ""
    (charts / "chart01.chr").write_text(f"<chart>\r\nsymbol=EURUSD.s\r\nperiod_type=1\r\n{expert}</chart>\r\n",
                                        encoding="utf-16")
    return data


def test_profile_reading(tmp_path):
    from mt5bridge import terminal

    plain = _fake_terminal(tmp_path / "a", with_ea=False)
    assert not terminal.profile_has_ea(plain) and terminal.profile_symbol(plain) == "EURUSD.s"
    assert terminal.profile_has_ea(_fake_terminal(tmp_path / "b", with_ea=True))
    assert terminal.install_dir(plain) is None  # no terminal64.exe
    (plain / "terminal64.exe").write_bytes(b"")
    assert terminal.install_dir(plain) == plain
    assert terminal.windows_path(Path("/x/drive_c/Program Files/MetaTrader 5")) == "C:\\Program Files\\MetaTrader 5"


def test_start_with_ea_never_starts_a_second_terminal(bridge_dir, fake_ea, tmp_path, monkeypatch):
    from mt5bridge import terminal

    assert terminal.start_with_ea(bridge_dir.parent.parent)["how"] == "already running"
    data = _fake_terminal(tmp_path, with_ea=False)
    (data / "terminal64.exe").write_bytes(b"")
    monkeypatch.setattr(terminal, "is_running", lambda install: True)
    monkeypatch.setattr(terminal, "_launch", lambda *a: pytest.fail("must not start MT5 while it runs"))
    res = terminal.start_with_ea(data / "MQL5", wait=0)
    assert res["attached"] is False and "Close MT5" in res["reason"] and res["user_steps"]

    started = []
    monkeypatch.setattr(terminal, "is_running", lambda install: False)
    monkeypatch.setattr(terminal, "_launch", lambda install, args: started.append(args))
    assert terminal.start_with_ea(data / "MQL5", wait=0)["attached"] is False  # nothing answers in a test
    ini = terminal.read_text(data / "MQL5" / "Files" / "mt5bridge" / "startup.ini")
    assert started == [["/config:MQL5\\Files\\mt5bridge\\startup.ini"]]
    assert "Expert=MT5Bridge" in ini and "Symbol=EURUSD.s" in ini


def test_setup_installs_skill_and_reports_compile(tmp_path, capsys, monkeypatch):
    import re

    from mt5bridge import install

    monkeypatch.setattr(install, "link_cli", lambda: {"on_path": "test"})
    data = _fake_terminal(tmp_path, with_ea=False)
    skills = tmp_path / "skills"
    assert cli_main(["--compact", "setup", "--mql5", str(data / "MQL5"), "--skills-dir", str(skills), "--no-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ea"][0]["compiled"] is False and "MetaEditor64.exe" in out["ea"][0]["reason"]
    assert (data / "MQL5" / "Experts" / "MT5Bridge.mq5").exists()
    text = (skills / "mt5bridge" / "SKILL.md").read_text("utf-8")
    assert re.search(r"^name: mt5bridge$", text, re.MULTILINE)
    assert text == (install.skill_source() / "SKILL.md").read_text("utf-8")
    install.install_skill(skills)  # updating an installed copy is fine
    foreign = tmp_path / "foreign"
    (foreign / "mt5bridge").mkdir(parents=True)
    (foreign / "mt5bridge" / "SKILL.md").write_text("---\nname: something-else\n---\n")
    with pytest.raises(BridgeError, match="not a copy"):
        install.install_skill(foreign)  # never overwrites a folder that is not this skill
    assert "something-else" in (foreign / "mt5bridge" / "SKILL.md").read_text()
