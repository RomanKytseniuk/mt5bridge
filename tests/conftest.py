import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_ea import FakeEA  # noqa: E402


@pytest.fixture
def bridge_dir(tmp_path):
    root = tmp_path / "MQL5" / "Files" / "mt5bridge"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def fake_ea(bridge_dir):
    with FakeEA(bridge_dir) as ea:
        yield ea


@pytest.fixture
def bridge(bridge_dir, fake_ea):
    from mt5bridge import Bridge

    return Bridge(bridge_dir=str(bridge_dir), timeout=5)
