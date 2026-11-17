import anyio
from mcp import Client

from mt5bridge import mcp_server

EXPECTED_TOOLS = {"status", "account", "list_trades", "get_trade", "get_candles", "get_live",
                  "get_symbol", "trade_stats", "chart_image"}


def _point_at(bridge_dir):
    mcp_server._state.update(bridge_dir=str(bridge_dir), timeout=5, bridge=None)


def test_tools_are_listed_and_read_only(bridge_dir, fake_ea):
    _point_at(bridge_dir)

    async def go():
        async with Client(mcp_server.server) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(go)
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_get_trade_returns_structured_json(bridge_dir, fake_ea):
    _point_at(bridge_dir)

    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool("get_trade", {"trade_id": "1001", "max_bars": 120})

    res = anyio.run(go)
    assert not res.is_error
    data = res.structured_content
    assert data["trade"]["exit_reason"] == "tp"
    assert data["excursions"]["mfe_points"] == 600.0
    assert data["candles"]["cols"][0] == "time"


def test_chart_image_is_png(bridge_dir, fake_ea):
    _point_at(bridge_dir)

    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool("chart_image", {"trade_id": "1001", "show_volume": True})

    res = anyio.run(go)
    assert not res.is_error
    assert res.content[0].type == "image" and res.content[0].mime_type == "image/png"


def test_errors_reach_the_model(bridge_dir):
    _point_at(bridge_dir)  # no fake EA running

    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool("list_trades", {})

    res = anyio.run(go)
    assert res.is_error
    assert "not running" in res.content[0].text
