# V2.4.0-alpha contract

Single source: `src/model_contract.py`. Live and offline replay call the same
pure history function with explicit UTC `as_of`; network access is an adapter.
V0 research scores remain historical hypotheses, not V2.4 scores.

| Field | Unit / formula / window | Missing policy |
|---|---|---|
| price changes | percentage points, closed close / close 12,48,288 bars ago - 1 | no price-dependent rule without actual price history |
| OI changes | collector fractions converted once to percentage points; OI notional value | missing required market context defers lifecycle evaluation |
| volume_z | last 12 closed 5m quote volumes vs up to 168 preceding disjoint hourly sums; population SD; minimum 24 hours | null and reason if insufficient/constant |
| funding_z | latest settlement vs preceding settlements within 7 calendar days; population SD; minimum 20 prior samples and >=6 days elapsed sample span; latest <=24h old | null and reason; no fabricated neutral sample |
| funding_acceleration | last two valid settlement rates' difference times 10000, basis points per settlement | null below two; not a time-normalized derivative |
| RV | population SD of closed 5m log returns times sqrt(288) | at least 24h of valid data |
| compression | current 24h RV / median of up to seven preceding disjoint 24h RV windows; minimum three baseline windows | null for insufficient/zero baseline; hypothesis threshold <0.8 gives +10 |

The 1500-bar live request contains about five days, not seven complete baseline
days. Coverage is explicit; this is not equivalent to the legacy rolling V0
baseline. Gaps, invalid prices or data older than ten minutes invalidate price
history. Funding failure is an explicitly missing optional feature.

Times: observation `observed_at` is the market fetch time; `data_as_of` bounds
history; `candle_closed_at` is the last complete input candle; evaluation time
is shared by classify/transition; run completion is after commit. These times
must not be silently substituted or displayed as equivalent.

Compression and missing-value behavior change model semantics. V2.4 is an
unvalidated research version, requiring replay/backtest and isolated shadow
verification before a live switch. Existing snapshots remain readable.
