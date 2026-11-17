"""Locate MetaTrader 5 data folders (the folder that holds MQL5/Files)."""

import os
import time
from pathlib import Path

from .errors import BridgeNotFound

ENV_DIR = "MT5BRIDGE_DIR"
HEARTBEAT_STALE_S = 5.0
BRIDGE_SUBDIR = "mt5bridge"

# Folders under a drive_c (Wine) or a Windows system drive where terminals live.
_TERMINAL_GLOBS = (
    "Program Files/*/MQL5",
    "Program Files (x86)/*/MQL5",
    "users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5",
)


def _wine_drives() -> list[Path]:
    home = Path.home()
    drives = [
        # Official MetaTrader 5 for macOS
        home / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c",
        # Common Wine / CrossOver prefixes on macOS and Linux
        home / ".wine/drive_c",
        home / ".mt5/drive_c",
    ]
    bottles = home / "Library/Application Support/CrossOver/Bottles"
    if bottles.is_dir():
        drives.extend(p / "drive_c" for p in bottles.iterdir())
    return [d for d in drives if d.is_dir()]


def _windows_mql5_dirs() -> list[Path]:
    found: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        found.extend(Path(appdata, "MetaQuotes/Terminal").glob("*/MQL5"))
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            found.extend(Path(base).glob("*/MQL5"))
    return found


def find_mql5_dirs() -> list[Path]:
    """Every MQL5 folder of every MT5 terminal found on this machine."""
    dirs: list[Path] = []
    for drive in _wine_drives():
        for pattern in _TERMINAL_GLOBS:
            dirs.extend(drive.glob(pattern))
    if os.name == "nt":
        dirs.extend(_windows_mql5_dirs())
    seen: set[Path] = set()
    result = []
    for d in dirs:
        r = d.resolve()
        if r not in seen and (r / "Files").is_dir():
            seen.add(r)
            result.append(r)
    return result


def heartbeat_age(bridge_dir: Path) -> float | None:
    """Seconds since the expert advisor last wrote status.json, or None if it never did."""
    try:
        return max(0.0, time.time() - (bridge_dir / "status.json").stat().st_mtime)
    except FileNotFoundError:
        return None


def resolve_bridge_dir(explicit: str | Path | None = None) -> Path:
    """Pick the bridge folder: explicit path, then $MT5BRIDGE_DIR, then auto-discovery.

    An explicit path may point at the bridge folder itself, at MQL5/Files or at MQL5.
    With several terminals: the first one (by folder path) whose expert advisor is running, so
    back-to-back calls stay on one account; if none runs, the one with the latest heartbeat.
    """
    given = explicit or os.environ.get(ENV_DIR)
    if given:
        p = Path(given).expanduser()
        if p.name == BRIDGE_SUBDIR:
            return p
        if p.name == "Files":
            return p / BRIDGE_SUBDIR
        if (p / "Files").is_dir():
            return p / "Files" / BRIDGE_SUBDIR
        return p

    candidates = [d / "Files" / BRIDGE_SUBDIR for d in find_mql5_dirs()]
    if not candidates:
        raise BridgeNotFound(
            "No MetaTrader 5 data folder found. Install MT5, or pass --dir / set "
            f"{ENV_DIR} to <MT5 data folder>/MQL5/Files."
        )
    ages = {c: heartbeat_age(c) for c in sorted(candidates, key=str)}
    running = [c for c, age in ages.items() if age is not None and age < HEARTBEAT_STALE_S]
    if running:
        return running[0]
    seen = sorted((age, str(c), c) for c, age in ages.items() if age is not None)
    return seen[0][2] if seen else candidates[0]
