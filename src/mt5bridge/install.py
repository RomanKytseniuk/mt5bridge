"""`mt5bridge setup`: everything outside Python in one idempotent step.

1. Copy the MT5Bridge expert advisor into MT5 and compile it with MetaEditor (skipped when up to date).
2. Copy the agent skill into the agent's skills folder, so agents know how to use the bridge.
3. Put `mt5bridge` on PATH (a link in ~/.local/bin, like pipx) so agents can run it from anywhere.
4. Start MT5 with the EA on a chart when MT5 is closed (see terminal.start_with_ea).
"""

import os
import re
import shutil
import sys
from importlib import resources
from pathlib import Path

from .discovery import find_mql5_dirs
from .errors import BridgeError, BridgeNotFound
from .terminal import EA_NAME, USER_STEPS, manual, read_text, run_windows_program, start_with_ea

SKILL_NAME = "mt5bridge"
AGENT_HOME = Path.home() / ".claude"  # Claude Code reads <it>/skills, and so does OpenCode


def ea_source() -> Path:
    return Path(str(resources.files("mt5bridge") / "mql5" / f"{EA_NAME}.mq5"))


def skill_source() -> Path:
    return Path(str(resources.files("mt5bridge") / "skills" / SKILL_NAME))


def packaged_ea_version() -> str | None:
    """EA_VERSION of the .mq5 shipped with this package, e.g. "1.0.1"."""
    try:
        text = ea_source().read_text("utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'#define\s+EA_VERSION\s+"([^"]+)"', text)
    return m.group(1) if m else None


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def _compile(terminal_dir: Path, source: Path) -> dict:
    if not (terminal_dir / "MetaEditor64.exe").exists():
        return {"compiled": False, "reason": f"MetaEditor64.exe not found in {terminal_dir}"}
    rel_src = source.relative_to(terminal_dir)
    rel_log = rel_src.with_suffix(".log")
    # Relative paths: MetaEditor under Wine mis-parses quoted absolute paths with spaces.
    args = ["/compile:" + str(rel_src).replace("/", "\\"), "/log:" + str(rel_log).replace("/", "\\")]
    try:
        run_windows_program(terminal_dir, "MetaEditor64.exe", args)
    except BridgeError as exc:
        return {"compiled": False, "reason": f"{exc}; compile MT5Bridge.mq5 in MetaEditor (F7)"}
    log_path = terminal_dir / rel_log
    log = read_text(log_path) if log_path.exists() else ""
    result_line = next((ln.strip() for ln in reversed(log.splitlines()) if ln.strip().startswith("Result:")), None)
    ok = bool(result_line and result_line.startswith("Result: 0 errors")) and source.with_suffix(".ex5").exists()
    out = {"compiled": ok, "result": result_line}
    if not ok:
        out["log_tail"] = log.splitlines()[-15:]
    return out


def install_ea(mql5_dir: str | Path | None = None) -> list[dict]:
    """Install into one terminal (mql5_dir) or into every terminal found."""
    targets = [Path(mql5_dir).expanduser()] if mql5_dir else find_mql5_dirs()
    if not targets:
        raise BridgeNotFound("No MetaTrader 5 terminal found. Pass the MQL5 folder with --mql5.")
    src = ea_source()
    report = []
    for mql5 in targets:
        if not (mql5 / "Experts").is_dir():
            raise BridgeError(f"{mql5} does not look like an MQL5 folder (no Experts subfolder)")
        dst = mql5 / "Experts" / f"{EA_NAME}.mq5"
        ex5 = dst.with_suffix(".ex5")
        item = {"mql5": str(mql5), "ea": str(dst)}
        same = dst.exists() and dst.read_bytes() == src.read_bytes()
        if same and ex5.exists() and ex5.stat().st_mtime >= dst.stat().st_mtime:
            item.update(compiled=True, updated=False)
        else:
            shutil.copyfile(src, dst)
            item.update(_compile(mql5.parent, dst), updated=True)
        report.append(item)
    return report


def _is_our_skill(folder: Path) -> bool:
    try:
        text = (folder / "SKILL.md").read_text("utf-8")
    except OSError:
        return False
    return re.search(rf"^name:\s*{SKILL_NAME}\s*$", text, re.MULTILINE) is not None


def install_skill(skills_dir: str | Path | None = None) -> dict:
    """Copy the skill into <skills_dir>/mt5bridge (default ~/.claude/skills), replacing an older copy."""
    if skills_dir is None and not AGENT_HOME.is_dir():
        return {"installed": None, "reason": f"{AGENT_HOME} not found: pass --skills-dir <your agent's skills folder>"}
    parent = Path(skills_dir).expanduser() if skills_dir else AGENT_HOME / "skills"
    target = parent / SKILL_NAME
    src = skill_source()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not _is_our_skill(target):
            raise BridgeError(f"{target} exists and is not a copy of the mt5bridge skill; remove it or pass --skills-dir")
        shutil.rmtree(target)
    parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    return {"installed": str(target)}


def link_cli() -> dict:
    """Make `mt5bridge` callable from any folder. Never replaces another mt5bridge already on PATH."""
    exe = Path(sys.executable).parent / ("mt5bridge.exe" if os.name == "nt" else "mt5bridge")
    found = shutil.which("mt5bridge")
    if found:
        return {"on_path": found}
    bindir = Path.home() / ".local" / "bin"
    path_dirs = [Path(p).expanduser() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.name == "nt" or not exe.exists() or bindir not in path_dirs:
        return {"on_path": None, "path": str(exe), "reason": f"add {exe.parent} to PATH, or call {exe} by full path"}
    link = bindir / "mt5bridge"
    if link.is_symlink() and not link.exists():
        link.unlink()  # left over from a removed install
    if link.exists():
        return {"on_path": None, "path": str(exe), "reason": f"{link} already exists: call {exe} by full path"}
    link.symlink_to(exe)
    return {"on_path": str(link), "linked_to": str(exe)}


def setup(mql5_dir: str | Path | None = None, *, start: bool = True, skill: bool = True,
          skills_dir: str | Path | None = None, wait: float = 90.0) -> dict:
    terminals = install_ea(mql5_dir)
    out: dict = {"ea": terminals}
    if skill:
        out["skill"] = install_skill(skills_dir)
        out["cli"] = link_cli()
    if not start:
        mt5 = None
    elif not all(t.get("compiled") for t in terminals):
        mt5 = {"attached": False, "reason": "the EA did not compile: open MQL5/Experts/MT5Bridge.mq5 in "
                                            "MetaEditor (in MT5: Tools -> MetaQuotes Language Editor) and press F7"}
    elif len(terminals) > 1:
        mt5 = manual("several MT5 terminals found: run `mt5bridge setup --mql5 <MQL5 folder>` for the one to use, "
                     "or attach the EA by hand there:")
    else:
        mt5 = start_with_ea(Path(terminals[0]["mql5"]), wait)
        if mt5.get("how") == "already running" and terminals[0].get("updated"):
            mt5 = {"attached": True, "how": "already running",
                   "reload": "MT5 still runs the previous EA build: close MT5 and run `mt5bridge setup` again"}
    if mt5 is not None:
        out["mt5"] = mt5
    if mt5 and not mt5.get("attached"):
        out["next"] = "Give the user mt5.user_steps (or mt5.reason), then check with `mt5bridge status`."
    elif mt5 and mt5.get("reload"):
        out["next"] = mt5["reload"]
    else:
        out["next"] = "Done. `mt5bridge status` should show ea_running: true."
    return out


__all__ = ["USER_STEPS", "install_ea", "install_skill", "link_cli", "packaged_ea_version", "setup", "version_tuple"]
