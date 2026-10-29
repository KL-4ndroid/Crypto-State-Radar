# Crypto State Radar — Autonomous Research Architecture

## Goal
Run continuously without any ChatGPT/OpenAI API dependency:

`discover -> record -> validate -> learn from stored evidence -> repeat`

ChatGPT is used only offline by the owner, after exporting accumulated datasets.

## Data layers

### 1. Raw market observations
Append-only 5m observations per Binance USDT-M perpetual:
- timestamp, symbol
- OHLCV
- open interest
- funding rate

Raw observations must never contain future-derived fields.

### 2. Feature snapshots
Calculated only from information available at snapshot time:
- price returns 15m/1h/3h/6h/12h/24h
- OI changes 5m/15m/1h/4h/24h
- OI acceleration
- volume acceleration / z-score
- funding z-score and funding acceleration
- price/OI elasticity and divergence
- volatility compression

### 3. Signal journal (immutable)
When V2.3 finds a candidate, write a new immutable signal row containing:
- signal_id
- detected_at
- symbol
- model_version
- pump_score / dump_score
- V2.2 state
- TTL
- complete feature snapshot
- reasons / rule hits
- detection price

Never rewrite the original signal snapshot after future data arrives.

### 4. State transitions
Append transitions rather than overwriting history:
`NORMAL -> ACCUMULATION -> PRESSURE -> EARLY_WARNING -> PRICE_CONFIRMATION -> LONG_READY/SHORT_READY -> TRIGGERED -> INVALIDATED/EXPIRED`

Each transition records previous_state, new_state, timestamp, price, OI, funding and reason.

### 5. Outcome validation
Each signal is independently evaluated at +1h/+3h/+6h/+12h/+24h/+3d/+7d.
Store:
- return
- MFE
- MAE
- realized future max/min
- event label
- validation timestamp

This layer may reference future data. It must never be merged back into the immutable detection snapshot.

## Runtime split

### Vercel
- web dashboard
- read APIs
- scheduled scan trigger
- lightweight candidate/state processing
- export endpoint

### Persistent database
Canonical structured store for signals, transitions, validations, experiments and recent observations.

### Object storage
Compressed raw 5m archives and research exports (Parquet/CSV/JSON).

### GitHub
Source code, configs, model-version definitions, schemas, backtest code and compact research summaries. Do not use GitHub as the primary high-frequency database.

## Research rule
Every score formula is versioned. A new formula creates a new model version; historical signal decisions are never retroactively recalculated in-place.

## Initial event definitions
- Pump event: future 24h maximum return >= +30%
- Dump event: future 24h minimum return <= -25%

These thresholds are configuration, not hard-coded research conclusions.

## No AI dependency
The production radar uses deterministic/statistical V2.x rules only. No OpenAI or ChatGPT API is required for collection, detection, state transitions, validation, storage, or export.
