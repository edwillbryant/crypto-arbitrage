"""The exchange wrapper exposes only an allow-list. Real control: API keys with withdrawals disabled."""

from __future__ import annotations

import ccxt
import pytest

from agent.execution.exchange_whitelist import ALLOWED_METHODS, ALLOWED_ORDER_PARAMS, SafeExchange


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def call(*a: object, **k: object) -> str:
            self.calls.append((name, a, k))
            return name

        return call


@pytest.mark.parametrize("cls", [ccxt.binance, ccxt.binanceusdm])
def test_every_allowed_method_exists_in_the_pinned_ccxt(cls: type) -> None:
    missing = [m for m in ALLOWED_METHODS if not callable(getattr(cls, m, None))]
    assert missing == []


@pytest.mark.parametrize("cls", [ccxt.binance, ccxt.binanceusdm])
def test_every_other_ccxt_method_is_refused(cls: type) -> None:
    safe = SafeExchange(Recorder())
    names = [n for n in dir(cls) if not n.startswith("_") and n not in ALLOWED_METHODS]
    assert len(names) > 2000  # includes ~460 withdraw/transfer/deposit/convert/borrow/sub-account methods
    for n in names:
        with pytest.raises(PermissionError):
            getattr(safe, n)


@pytest.mark.parametrize("name", ["withdraw", "transfer", "sapiPostAssetTransfer", "sapi_post_capital_withdraw_apply",
                                  "create_sub_account", "borrow_margin", "fetch_deposit_address", "set_leverage"])  # fmt: skip
def test_named_money_movement_is_refused(name: str) -> None:
    with pytest.raises(PermissionError):
        getattr(SafeExchange(Recorder()), name)


def test_allowed_calls_pass_through() -> None:
    raw = Recorder()
    safe = SafeExchange(raw)
    assert safe.fetch_balance() == "fetch_balance"
    safe.create_order(
        "BTC/USDT:USDT", "limit", "buy", 0.001, 50000.0, {"timeInForce": "IOC", "reduceOnly": False}
    )
    assert raw.calls[-1][0] == "create_order"


def test_unknown_order_params_are_refused() -> None:
    safe = SafeExchange(Recorder())
    with pytest.raises(PermissionError, match="param"):
        safe.create_order("BTC/USDT", "limit", "buy", 1, 1, {"type": "MAIN_UMFUTURE"})
    assert {"timeInForce", "reduceOnly", "newClientOrderId"} <= ALLOWED_ORDER_PARAMS


def test_wrapper_has_no_public_handle_on_the_raw_client() -> None:
    safe = SafeExchange(Recorder())
    assert [n for n in dir(safe) if not n.startswith("_") and n not in ALLOWED_METHODS] == []
