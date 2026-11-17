"""mt5bridge: read-only MetaTrader 5 bridge. A CLI utility with an MCP server on top."""

__version__ = "0.1.0"

from .errors import BridgeError, BridgeNotFound, BridgeTimeout, EANotRunning, RemoteError  # noqa: E402
from .service import Bridge  # noqa: E402

__all__ = ["Bridge", "BridgeError", "BridgeNotFound", "BridgeTimeout", "EANotRunning", "RemoteError", "__version__"]
