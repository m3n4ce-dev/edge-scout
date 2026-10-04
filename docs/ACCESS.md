# Venue access (checked 2026-10-04 from a US connection)

"Data" = the public market-data API answered from here. "Trade (US)" = a US resident may trade there, per the
venue's terms. Re-check before relying on either; both change.

| Venue | Data | Trade (US) | Used for |
|---|---|---|---|
| Coinbase (spot) | ✅ | ✅ | spot quotes |
| Coinbase Derivatives (CDE: US perps + dated futures) | ✅ | ✅ (via Coinbase Financial Markets) | funding, basis |
| Kraken (spot) | ✅ | ✅ | spot quotes |
| Kraken US perps (Bitnomial) | ? | ✅ (since 2026-06) | not wired yet |
| Kraken Futures (international `PF_`) | ✅ | ❌ | funding (signal) |
| Gemini | ✅ | ✅ | spot quotes |
| Bitstamp | ✅ | ✅ | spot quotes |
| Binance.US | ✅ | ✅ | spot quotes |
| Kalshi | ✅ | ✅ | prediction markets (M1) |
| Polymarket | ✅ | check (US access changed in 2025–26) | prediction markets (M1) |
| OKX | ✅ | ❌ | funding (signal) |
| Hyperliquid | ✅ | ❌ (terms) | funding (signal) |
| dYdX v4 | ✅ | ❌ | funding (signal) |
| Deribit | ✅ | ❌ | dated futures, options (implied probabilities) |
| Binance (global) | ❌ HTTP 451 | ❌ | — |
| Bybit | ❌ HTTP 403 | ❌ | — |
