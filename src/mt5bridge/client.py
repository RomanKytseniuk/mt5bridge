"""Low-level transport: talks to the MT5Bridge expert advisor through files.

Protocol (version 1), all inside <MQL5>/Files/mt5bridge:
  req/<id>.req    request written by the client: UTF-8 lines "key=value", first line "cmd=<name>"
  resp/<id>.json  response written by the EA: {"id", "cmd", "ok", "data" | "error"}
  status.json     heartbeat the EA rewrites every second

Both sides write to a temporary name first and rename, so a reader never sees half a file.
Several clients (the CLI and the MCP server) can use one EA at the same time.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .discovery import HEARTBEAT_STALE_S, heartbeat_age, resolve_bridge_dir
from .errors import BridgeError, BridgeTimeout, EANotRunning, RemoteError

STALE_RESPONSE_S = 120.0


class BridgeClient:
    def __init__(self, bridge_dir: str | Path | None = None, timeout: float = 15.0, poll: float = 0.02):
        self.bridge_dir = resolve_bridge_dir(bridge_dir)
        self.timeout = timeout
        self.poll = poll

    @property
    def req_dir(self) -> Path:
        return self.bridge_dir / "req"

    @property
    def resp_dir(self) -> Path:
        return self.bridge_dir / "resp"

    def heartbeat(self) -> dict[str, Any]:
        age = heartbeat_age(self.bridge_dir)
        info = None
        if age is not None:
            try:
                info = json.loads((self.bridge_dir / "status.json").read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                info = None
        return {
            "bridge_dir": str(self.bridge_dir),
            "ea_running": age is not None and age < HEARTBEAT_STALE_S,
            "heartbeat_age_s": None if age is None else round(age, 1),
            "heartbeat": info,
        }

    def _ensure_alive(self) -> None:
        age = heartbeat_age(self.bridge_dir)
        if age is None or age >= HEARTBEAT_STALE_S:
            when = "never" if age is None else f"{age:.0f}s ago"
            raise EANotRunning(
                f"MT5Bridge expert advisor is not running (last heartbeat: {when}). "
                "Open MetaTrader 5 and attach MT5Bridge to any chart. "
                f"Bridge folder: {self.bridge_dir}"
            )

    def _cleanup_stale(self) -> None:
        now = time.time()
        for folder in (self.resp_dir, self.req_dir):
            if not folder.is_dir():
                continue
            for f in folder.iterdir():
                try:
                    if now - f.stat().st_mtime > STALE_RESPONSE_S:
                        f.unlink()
                except OSError:
                    pass

    def request(self, cmd: str, **params: Any) -> Any:
        """Send one command and return the "data" part of the answer."""
        self._ensure_alive()
        self.req_dir.mkdir(parents=True, exist_ok=True)
        self.resp_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale()

        rid = uuid.uuid4().hex
        lines = [f"cmd={cmd}"]
        for key, value in params.items():
            if value is None:
                continue
            text = str(value)
            if "\n" in text or "\r" in text:
                raise BridgeError(f"parameter {key!r} must not contain line breaks")
            lines.append(f"{key}={text}")
        tmp = self.req_dir / f"{rid}.tmp"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.req_dir / f"{rid}.req")

        resp = self.resp_dir / f"{rid}.json"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if resp.exists():
                try:
                    payload = json.loads(resp.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    time.sleep(self.poll)
                    continue
                resp.unlink(missing_ok=True)
                if not payload.get("ok"):
                    raise RemoteError(payload.get("error") or "unknown error from MT5Bridge")
                return payload.get("data")
            time.sleep(self.poll)

        (self.req_dir / f"{rid}.req").unlink(missing_ok=True)
        self._ensure_alive()
        raise BridgeTimeout(
            f"MT5Bridge did not answer '{cmd}' within {self.timeout:.0f}s. MT5 may still be downloading "
            "history for this request: retry once, then ask for a shorter period or a higher timeframe."
        )
