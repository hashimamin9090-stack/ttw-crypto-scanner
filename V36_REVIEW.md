# V3.6.0 review and decision record

The 29 October review recovered 94 delivered alert identities from retained
22–29 October logs. This is not the entire period since the 10 October audit.
The original TP1/SL1 tracker contained 11 target-first, 40 stop-first, 35
unknown, five expired and three open paths. Pattern confirmation is a
different measure. Missing histories must remain visible.

The current strategy did not establish a dependable edge in this sample.
Some promising associations were structure-based TP1 and the causal C1/C2
pullback route. These are discovery findings, confounded by timeframe,
market and overlapping alerts, and not validated filter effects.

## Timeframe distinction

Intraday results must not be used as the success rate of the daily/weekly
pattern. The 15 delivered daily-and-higher signals originally had seven
SL1 TP1 stops and eight unknowns. Their longer horizons were especially
exposed to the previous permanent-unknown reconnect policy. The available
data establishes neither a 60–70% higher-timeframe win rate nor that the
same entry filter is appropriate on all horizons.

Accordingly, the extra midpoint-reclaim observation applies only to hourly
BROADER_REVERSAL candidates and never changes delivery eligibility. Daily-and-higher entry thresholds, geometry
tolerance and optional confluence remain as before. The 36 timeframes,
including long multi-day/week/month frames, remain enabled where source
history is sufficient. Performance is now reported by horizon and feed.

## Observational entry comparison

Reclaiming half of the completed C2 body by one tick is a candidate causal
measure of price recovery for a broad fade, recorded as observation only. It uses no C3 close or future swing, and
introduces no timer or arbitrary indicator. Pullback/C1-approach routes
retain their current evidence. This observation is a research hypothesis,
not an eligibility requirement, fitted probability or guarantee of more winners.

A frozen-quote check of the 94 retained snapshots held 28 original intraday
quotes: 12 had recorded SL1 stops, three had recorded SL1 targets, 11 were
unknown, and two expired. That is why this proposed gate was not activated: it would sacrifice some
formerly winning entry quotes. Existing alerts continue at the existing
entry trigger while the extra evidence is recorded alongside them. This snapshot
check does not recreate later alert timing or constitute a backtest of the
new strategy. There is no claimed improved live win rate.

## Accounting repair

New outcome histories retain origin and per-path recovery cursors. Data
recovery runs in the existing paced market-data lanes, with at most two
delivered records per feed per pass, 24-hour hourly chunks, and at most one
minute-refinement request span per record. Existing endpoint pacing and
exchange cooldowns remain in force. The worker does not place orders.

Only contiguous completed same-feed data may prove a target-before-stop
outcome. An initial range containing pre-entry contact is not credited as
a new event. A range containing both barriers is refined to minutes;
remaining minute ambiguity stays unknown. Verified no-contact intervals
advance a persistent cursor. Expiry requires coverage to the original
deadline. Existing TP/STOP outcomes and published prices are immutable.
Historical gap metadata and interval proof are retained in the outcome log.

The primary exit policy remains TP1 before known opposing structure when
both stop-risk requirements pass. TP2 remains an extension, not a substitute
for TP1 success. Stop policies must be evaluated separately at equal money
risk; SL2 is not necessarily the wider stop.

## Validation and next evidence

The full 323-case suite passes. It checks price symmetry, market routing,
preserved higher-timeframe rules, the previously captured BTC no-level
regression, immutable stops, pre-entry contamination, incomplete/gapped
bars, coarse ambiguity, finer recovery, expiry, restart persistence,
worker request bounds and recipient access for performance summaries.

These are correctness checks. Later untouched market episodes must establish
whether net target-before-stop results improve after costs, including missed
opportunities, expired paths and unknowns. Overlapping BTC timeframes or
the same ZEC move must not be treated as independent evidence. The original
10–22 October cohort still requires the saved bot state to complete its audit.

## Delivery efficiency

Processing now prioritizes BTC, ZEC, HYPE and the stock contracts, with 3H+
ahead of 2H within the focus group. This changes processing order, not the
universe or the number of timeframes. Others remain eligible.

The previous two-tick chart-render cancellation is replaced by a fresh
entry quote followed by the full current quality and risk checks. Stops and
target prices stay frozen across rendering. The chart explicitly labels
its snapshot quote/time; the caption and ledger contain the fresh entry and
its recomputed percentages. There is no permission to send a stale quote,
wrong-colour C3, broken geometry, a breached frozen stop, a previously
impulsed setup, inadequate target room or invalid reward/risk.
