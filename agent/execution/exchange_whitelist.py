"""Allow-list wrapper around a ccxt exchange client.

The pinned ccxt Binance classes expose ~2,500 public methods, ~460 of them for withdrawals, transfers,
deposits, conversions, borrowing or sub-accounts (plus every raw endpoint). A block-list could never be
complete, so this wrapper allows a short list and refuses everything else, including unknown order params.

This is defence in depth against accidental misuse. The real control is that API keys are created
with withdrawals disabled and IP restriction on.
"""

from __future__ import annotations

from typing import Any

ALLOWED_METHODS = frozenset({
    "load_markets", "fetch_time", "fetch_balance", "fetch_positions", "fetch_open_orders", "fetch_order",
    "fetch_my_trades", "fetch_order_book", "fetch_ohlcv", "fetch_funding_rate", "fetch_ticker",
    "create_order", "cancel_order", "cancel_all_orders",
})  # fmt: skip
ALLOWED_ORDER_PARAMS = frozenset(
    {"timeInForce", "reduceOnly", "newClientOrderId", "clientOrderId", "postOnly"}
)


class SafeExchange:
    __slots__ = ("_SafeExchange__raw",)

    def __init__(self, raw: Any) -> None:
        object.__setattr__(self, "_SafeExchange__raw", raw)

    def __getattr__(self, name: str) -> Any:
        if name not in ALLOWED_METHODS:
            raise PermissionError(f"exchange method {name!r} is not on the allow-list")
        raw = object.__getattribute__(self, "_SafeExchange__raw")
        method = getattr(raw, name)
        if name != "create_order":
            return method

        def create_order(*args: Any, **kwargs: Any) -> Any:
            """ccxt signature: (symbol, type, side, amount, price=None, params=None)."""
            params = kwargs.get("params", args[5] if len(args) > 5 else None)
            unknown = set(params or {}) - ALLOWED_ORDER_PARAMS
            if unknown:
                raise PermissionError(f"order param(s) {sorted(unknown)} are not on the allow-list")
            return method(*args, **kwargs)

        return create_order

    def __setattr__(self, name: str, value: object) -> None:
        raise PermissionError("SafeExchange is read-only")

    def __dir__(self) -> list[str]:
        return sorted(ALLOWED_METHODS)
