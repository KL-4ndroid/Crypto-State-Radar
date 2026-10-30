# Setup lifecycle V2.4

| Previous / condition (in priority order) | Result |
|---|---|
| Invalid or missing required data | Preserve last valid state; mark pending; never fabricate trigger/invalidation |
| READY and now >= fixed expiry | EXPIRED, even if a new candidate still confirms |
| READY and opposite price/OI structure or opposite READY | INVALIDATED |
| READY and same confirmed candidate, price crosses original ready_price | TRIGGERED |
| READY otherwise, including temporary lower score | Keep setup and original price/expiry until a terminal condition |
| Terminal and WATCH candidate | Keep terminal record, no new cycle |
| Terminal and new warning/pressure/READY | New cycle; reset extremes and setup anchors |
| Other states | Follow candidate |

Re-entry after EXPIRED is evaluated on a subsequent valid scan, never silently
within the expiry transition. A terminal signal cannot renew its own setup.
READY anchors change only on entry to a new READY setup. Pending checks update
`checked_at` but not the last successful `updated_at`. Legacy records have null
new metadata fields; schema 1 remains backward compatible (additive fields).
Frontend freshness is a view of validity, not a rewrite of stored history.

V2.4.1: a warning that returns after WATCH starts a new cycle; it must not be
merged into the earlier warning episode in evaluation. A legacy READY without
expiry may derive it only from the original aware `entered_at` plus 240 minutes.
Missing/invalid entry or price anchor remains pending for review, never renewed
from the current time and never counted as a successful evaluation.
