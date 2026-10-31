# Crypto State Radar

Binance USDT-M perpetual futures research project. The **v3.3.0 research release** includes isolated causal research contracts, dual state machines, replay tools and a bounded local acquisition pilot.

## v3.3.0 research release

Released research scope: PRs [#20](https://github.com/KL-4ndroid/Crypto-State-Radar/pull/20) and [#21](https://github.com/KL-4ndroid/Crypto-State-Radar/pull/21). All supplied V3.3 rules remain **EXPERIMENTAL / PRE_VALIDATION**. The production signal model remains **V2.4.1-alpha**; the release does not activate V3 collection, apply its production migrations or promote a Champion.

See [release notes](docs/v33/09_research_release.md) for delivered features, verification and remaining work, and [current status](docs/current-status.md) for the production system. This is a release of the existing research work, not a completed or validated V3.3 trading model.

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

## Historical V2 implementation checkpoint

The following checkpoint records earlier release preparation. Current release status is documented above and in [current status](docs/current-status.md).

The previous production baseline was commit `5816d3a`. The V2.4.1 runtime and
Dashboard changes are prepared for the user-authorized main release. See
[release checklist](docs/production-release.md) for environment isolation,
acceptance, scheduler activation and rollback. See [checkpoint](docs/implementation-checkpoint.md)
for scope, acceptance evidence, remaining work, and safe continuation steps.
See [continuation acceptance](docs/continuation-acceptance.md) for subsequent
V2.4.1 fixes and real PostgreSQL/Redis, market and replay verification.
See [Dashboard resource optimization](docs/dashboard-resource-optimization-implementation.md)
for the manifest/ETag read contract, shared cache, browser lifecycle and isolated
Neon/Vercel preview acceptance. Local implementation, preview verification and
production release are recorded separately. The user subsequently authorized
committing, pushing and merging this completed work to main.

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

The existing hourly full-market scheduler has not been changed. The research
worker now has an opt-in five-minute continuation/validation tick, disabled
unless `CSR_RESEARCH_WORKER_ENABLED=true` and its isolated URL/token are set in
GitHub. Activation is an explicit release step after the production API is verified.
Full scans select at most 25 deep candidates;
continuations can evaluate up to 100, with the same 40-second upstream deadline.

## Dashboard readability

Primary market text uses 16–18px type with larger symbols and counts. The overview,
priority signals, evaluation times, pending work and data warnings remain visible.
Operational metrics and lifecycle explanations are collapsed under “掃描進度與系統資訊”.
The main table shows symbol/direction, state, pump/dump pressure and evaluation time;
select a symbol for OI, funding, reasons and additional time/anchor records.
