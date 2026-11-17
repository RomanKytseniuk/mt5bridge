"""Errors raised by mt5bridge."""


class BridgeError(Exception):
    """Base error. The message is meant to be shown to a human or an AI agent as is."""


class BridgeNotFound(BridgeError):
    """No MetaTrader 5 data folder (MQL5/Files) was found."""


class EANotRunning(BridgeError):
    """The MT5Bridge expert advisor is not attached to a chart, or MT5 is closed."""


class BridgeTimeout(BridgeError):
    """The expert advisor did not answer in time."""


class RemoteError(BridgeError):
    """The expert advisor answered with an error."""
