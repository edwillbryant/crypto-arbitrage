`fapi_*.json` are real responses recorded from testnet.binancefuture.com on 2026-10-21. `spot_book_btc.json` is
hand-written in the documented spot `/api/v3/ticker/bookTicker` format (the spot testnet isn't reachable from the
build environment). Spot bookTicker has no timestamp, so the feed uses the receive time.
