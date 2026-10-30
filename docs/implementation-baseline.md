# Implementation baseline — 2026-10-30

## Identity

- Repository: KL-4ndroid/Crypto-State-Radar
- GitHub main: `5816d3a3806c2f68c2554f21b407bb620e278b2d`
- Production alias: https://crypto-state-radar.vercel.app
- Verified through authenticated Vercel deployment inspection: `dpl_FM1EvyiAhAVisG7Xaj5cVmi9xyr2`, READY, production, sin1, same Git SHA.
- Project metadata still reports framework `python`; repository routing explicitly sets `framework: null`. Do not change the preset based on metadata alone.
- Working branch: `fix/production-feature-enrichment`. Existing local work was uncommitted and not deployed; baseline tests: 43 passed.
- Scheduler timing is explicitly outside this implementation's verification scope.

## Existing uncommitted changes grouped for review

| Group | Files | Prior local behavior / remaining risk |
|---|---|---|
| Data quality | live_features.py, test_live_features.py | Closed contiguous candles, separate volume windows; compression still reciprocal to research definition |
| Model wiring | run_cycle.py, test_run_cycle.py | Enrichment of at most 25 symbols; at most 10 active; no complete pinning or persistent backlog; local version override |
| Validation | validate_signals.py, test_validation_cycle.py | Reject missing intervals and exclude post-horizon candles; boundary coverage not recorded |
| Documentation | README.md | Describes local changes and limits; not evidence of deployment |

## Fixed acceptance scenarios

`tests/fixtures/market_cases.json` records stable up, down, flat, missing-data,
new-listing, API-error and existing-READY cases. `tests/test_replay.py` replays the
same cases against the pinned main implementation and current contracts, and
writes no production data. Raw candle builders are deterministic, contain no
randomness and use explicit UTC as-of times.

Expected baseline: pinned main's actual `_features` feed always classifies valid
market observations as WATCH, including high OI acceleration (maximum 24 points).
New enriched results may warn, but are not evidence of predictive accuracy.

## Release gates

1. Contract fixtures and legacy snapshot decoding pass.
2. Lifecycle sequence, pending/failure and overflow tests pass.
3. Transaction rollback, idempotency and concurrent commit tests pass.
4. Event evaluation excludes unmatured windows and separates selection misses.
5. Browser checks on desktop/mobile, isolated preview DB, measured upstream
   request/runtime limits and shadow comparison precede production promotion.

No production database exists yet. Local acceptance does not satisfy the live
PostgreSQL, preview, throughput, backtest-quality or production release gates.
