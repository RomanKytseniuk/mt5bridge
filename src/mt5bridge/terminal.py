"""One MetaTrader 5 terminal: its files, its process, and starting it with the MT5Bridge EA on a chart.

MT5 cannot attach an expert to a running terminal from outside. At start it can: when the last
profile already has a chart with the EA, it comes back by itself; otherwise a /config file with a
[StartUp] section opens a chart with the EA attached.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .discovery import BRIDGE_SUBDIR, HEARTBEAT_STALE_S, heartbeat_age
from .errors import BridgeError

EA_NAME = "MT5Bridge"
MAC_APP = Path("/Applications/MetaTrader 5.app")
MAC_WINE = MAC_APP / "Contents/SharedSupport/wine/bin/wine64"
MAC_APP_TERMINAL = (Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c"
                    / "Program Files/MetaTrader 5")
STARTUP_INI = "startup.ini"
USER_STEPS = [
    "Open MetaTrader 5 and any chart.",
    "Open the Navigator: View -> Navigator (Ctrl+N).",
    f"Under Expert Advisors, drag {EA_NAME} onto the chart.",
    "Press OK. Algo Trading can stay off: the bridge never trades.",
    f"Check that the {EA_NAME} name and a cap icon appear in the chart's top-right corner.",
]


def read_text(path: Path) -> str:
    """MT5 writes its ini, chart and log files as UTF-16; ours are UTF-8."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def drive_c(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if parent.name == "drive_c":
            return parent
    return None


def windows_path(path: Path) -> str:
    """The path as programs inside Wine see it (C:\\...); unchanged on Windows."""
    drive = drive_c(path)
    if os.name == "nt" or drive is None:
        return str(path)
    return "C:\\" + str(path.relative_to(drive)).replace("/", "\\")


def run_windows_program(cwd: Path, program: str, args: list[str], *, detach: bool = False,
                        timeout: float = 300) -> None:
    """Run a Windows program natively or under Wine (MT5's own Wine on macOS)."""
    env = dict(os.environ)
    if os.name == "nt":
        cmd = [str(cwd / program) if not Path(program).is_absolute() else program, *args]
    else:
        drive = drive_c(cwd)
        wine = str(MAC_WINE) if MAC_WINE.exists() else shutil.which("wine64") or shutil.which("wine")
        if drive is None or not wine:
            raise BridgeError("Wine not found: MetaTrader 5 has to be started by hand")
        env.update(WINEPREFIX=str(drive.parent), WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree=")
        external = Path(wine).parent.parent / "lib" / "external"
        if external.is_dir():  # what the MetaTrader 5 app sets for its Wine
            env["DYLD_FALLBACK_LIBRARY_PATH"] = str(external)
        cmd = [wine, program, *args]
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if detach:
        if os.name == "nt":
            subprocess.Popen(cmd, cwd=cwd, env=env, creationflags=0x00000008 | 0x00000200, **io)
        else:
            subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True, **io)
        return
    subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, **io)


def install_dir(data_dir: Path) -> Path | None:
    """Folder with terminal64.exe: the data folder itself (Wine, portable) or the one in origin.txt."""
    if (data_dir / "terminal64.exe").exists():
        return data_dir
    origin = data_dir / "origin.txt"
    if origin.exists():
        folder = Path(read_text(origin).strip().strip("\x00"))
        if (folder / "terminal64.exe").exists():
            return folder
    return None


def is_running(install: Path) -> bool:
    exe = windows_path(install / "terminal64.exe").lower()
    if os.name == "nt":
        script = "Get-Process terminal64 -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=30).stdout
        return any(line.strip().lower() == exe for line in out.splitlines())
    out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=30).stdout
    return any(exe in line.lower() for line in out.splitlines())


def _profile_charts(data_dir: Path) -> list[str]:
    profile = "Default"
    try:
        m = re.search(r"^ProfileLast=(.+?)\s*$", read_text(data_dir / "config" / "common.ini"), re.MULTILINE)
        profile = m.group(1) if m else profile
    except OSError:
        pass
    charts = sorted((data_dir / "MQL5" / "Profiles" / "Charts" / profile).glob("*.chr"))
    texts = []
    for chart in charts:
        try:
            texts.append(read_text(chart))
        except OSError:
            pass
    return texts


def profile_has_ea(data_dir: Path) -> bool:
    return any(re.search(rf"<expert>\s*name={EA_NAME}\s", text) for text in _profile_charts(data_dir))


def profile_symbol(data_dir: Path) -> str | None:
    for text in _profile_charts(data_dir):
        m = re.search(r"^symbol=(\S+)", text, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def ea_running(bridge_dir: Path) -> bool:
    age = heartbeat_age(bridge_dir)
    return age is not None and age < HEARTBEAT_STALE_S


def _launch(install: Path, args: list[str]) -> None:
    if sys.platform == "darwin" and not args and MAC_APP.exists() and install == MAC_APP_TERMINAL:
        # The app's own launcher: same Dock icon and window as usual. It drops extra arguments,
        # so a /config start has to go through Wine directly.
        subprocess.Popen(["open", "-a", str(MAC_APP)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    run_windows_program(install, windows_path(install / "terminal64.exe"), args, detach=True)


def manual(reason: str) -> dict:
    return {"attached": False, "reason": reason, "user_steps": USER_STEPS}


def start_with_ea(mql5: Path, wait: float = 90.0) -> dict:
    """Start MetaTrader 5 so that MT5Bridge runs on a chart. Never touches a terminal that is open."""
    data = mql5.parent
    bridge = mql5 / "Files" / BRIDGE_SUBDIR
    if ea_running(bridge):
        return {"attached": True, "how": "already running"}
    install = install_dir(data)
    if install is None:
        return manual(f"terminal64.exe not found for {data}: start MetaTrader 5 yourself")
    if is_running(install):
        return manual("MetaTrader 5 is open, and MT5 cannot attach an expert from outside while it runs. "
                      "Close MT5 and run `mt5bridge setup` again, or attach it by hand:")
    if profile_has_ea(data):
        args, how = [], "MT5 started; its last profile already has the EA on a chart"
    else:
        bridge.mkdir(parents=True, exist_ok=True)
        ini = bridge / STARTUP_INI
        symbol = profile_symbol(data)
        lines = ["[StartUp]", f"Expert={EA_NAME}", *([f"Symbol={symbol}"] if symbol else []), "Period=H1"]
        ini.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16")
        # Under Wine a path relative to the terminal folder avoids quoting trouble with spaces.
        cfg = str(ini.relative_to(install)).replace("/", "\\") if install == data else str(ini)
        args, how = [f"/config:{cfg}"], "MT5 started with a chart that has the EA attached"
    _launch(install, args)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ea_running(bridge):
            return {"attached": True, "how": how}
        time.sleep(1)
    return manual(f"MT5 was started, but {EA_NAME} did not report within {wait:.0f}s. "
                  "If MT5 is now open without it, attach it by hand:")
