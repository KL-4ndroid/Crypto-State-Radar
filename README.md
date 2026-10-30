# Crypto State Radar

Binance USDT-M perpetual futures research project for V2.2 execution states and V2.3 early-warning / backtest research.

## Goals

- Preserve recent Binance futures historical datasets before exchange retention windows expire.
- Detect pump / dump precursors using 5m raw data and multi-horizon aggregation.
- Separate early-warning discovery (V2.3) from execution confirmation (V2.2).
- Backtest event windows before large moves and measure precision, recall, false positives and lead time.

## Core signals

### V2.2 execution layer

- WATCH
- PRICE CONFIRMATION
- LONG READY / SHORT READY
- TRIGGERED
- INVALIDATED
- EXPIRED

Primary concepts: state transition, price confirmation, OI retreat, signal TTL.

### V2.3 early-warning layer

Candidate features:

- OI acceleration
- Funding acceleration
- Price / OI divergence and elasticity
- Volume acceleration
- Volatility compression
- OI / market cap
- Delta OI / market cap
- liquidation asymmetry (when reliable historical data is available)

## Data layout

```text
data/
  raw/
    klines/5m/YYYY-MM-DD/SYMBOL.csv.gz
    oi/5m/YYYY-MM-DD/SYMBOL.csv.gz
    funding/YYYY-MM/SYMBOL.csv.gz
  derived/
    features/5m/YYYY-MM-DD/SYMBOL.parquet
    events/YYYY-MM/events.parquet
  manifests/
    YYYY-MM-DD.json
src/
  collect_binance_history.py
  build_features.py
  detect_events.py
  backtest.py
config/
  backtest.yaml
.github/workflows/
```

Raw data should be treated as append-only. Derived files can be regenerated.

## Initial event definitions

- Pump event: max forward return over 24h >= +30%
- Dump event: min forward return over 24h <= -25%
- Evaluation windows before an event: 1h / 3h / 6h / 12h / 24h

These thresholds are research defaults, not trading advice and can be tuned after measuring class balance and false-positive rates.

## Implementation checkpoint (not deployed)

Production remains at commit `5816d3a`. The local V2.4 work is a reviewable
checkpoint, not a production release. See [checkpoint](docs/implementation-checkpoint.md)
for scope, acceptance evidence, remaining work, and safe continuation steps.

- [Verified production baseline](docs/implementation-baseline.md)
- [Feature/model contract](docs/model-contract.md)
- [Lifecycle transition contract](docs/lifecycle-contract.md)
- [Pinned baseline vs new-model synthetic replay](docs/acceptance-replay.json)

The scanner now supports a transactional local ledger and an optional PostgreSQL
adapter. Deployed new-runtime requests require `CSR_RESEARCH_DATABASE_URL` and a
matching `CSR_RESEARCH_ENVIRONMENT`; they fail closed instead of using `/tmp` as
a durable database. The schema must be migrated explicitly. Vercel defaults to
shadow mode. Do not deploy this checkpoint over the live app before isolated
PostgreSQL and preview acceptance. See `.env.example` for configuration names.

The existing hourly scheduler has not been changed. The new research workflow is
manual and disabled unless its repository enable flag and isolated URL are set.
It does not yet provide activated automatic forward validation.
