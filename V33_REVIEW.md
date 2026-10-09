# TTW 3.3 review and release rationale

The core problem is premature reversals that subsequently extend through the
frozen C3 stop. The objective is more useful confirmed opportunities and better
target-before-stop outcomes, with honest coverage reporting. Increasing the
confirmation percentage by hiding most opportunities is insufficient.

## What the saved bot results establish

The pre-upgrade running instance held 80 distinct alert identities: 58 INVALID,
19 CONFIRMED and 3 unresolved. Its resolved invalidation rate was 58/77, or
75.3%. This is the current saved cohort, not the bot's entire history. Earlier
redeploys lost local state; a separate reconstruction from later alert candle
snapshots found 48/60 invalid, but those samples must not be pooled blindly.

Among the 58 saved invalid patterns, 50 breached SL1, 46 failed final wick
geometry and 42 closed in the wrong colour. Reasons overlap. Thirty invalid
and five confirmed alerts had only a one-tick reversal body at delivery.

A snapshot-only application of the initial reclaim and target-risk checks
would accept five confirmed and five invalid original entries. Fourteen
confirmed entries were initially below the reclaim threshold; all fourteen
later reached that price distance somewhere in their C3 OHLC. This does not
prove a valid later entry: OHLC alone does not establish event order, fresh
BTC context, maintained geometry or early-entry eligibility. The old eight-bar
snapshots also cannot retrospectively validate the new 32-bar location rules.
Therefore no complete new-version backtest or improved profit estimate is
claimed. Retaining watches and recording a simultaneous baseline are essential.

Confirmation is a C3 pattern verdict, not a trade outcome. The previous bot
did not establish ordered TP-before-stop results beyond C3. Neither its
confirmation ratio nor its observed maximum excursion proves profitability.

## Why these changes

1. Require a small, volatility-scaled body reclaim rather than a one-tick flip.
   Keep the existing upper early-entry limit and no timed hold. The two-tick
   floor is compatible with the existing eight-tick impulse floor.
2. Treat live opposing BTC expansion as a veto. A permanently bearish label
   would reject genuine market turns; acute expansion and the descriptive 1H
   trend are therefore separate. Missing BTC data holds the candidate.
3. Demand a pre-existing support/resistance location. C1/C2 cannot invent the
   level that supposedly validates their own reversal. Use completed setup
   and parent candles, with explicit confirmation times and bounded proximity.
4. Reject unattractive target-risk geometry and cramped opposing-level room.
   A successful-looking C3 can still have a stop wider than the requested TP.
   These checks target trade usefulness, not just pattern appearance.
5. Preserve eligible slopes and frozen stops. A C2 wick can already be beyond
   the entry in the wrong direction for a protective stop. In that case retain
   the outer-wick alternative and label it accurately. Never widen stops after
   the alert is delivered.
6. Record independent, chronological outcomes for both stops and both targets.
   Unknown periods remain UNKNOWN. Preserve earlier target hits after a later
   stop. Do not discard observations merely because C3 ended or a coin left
   the top-25 universe.
7. Observe the unfiltered first entry privately. Compare accepted and held
   setups, including missed winners, using the same market period and horizon.

No timeframe is disabled from this small sample. All original intervals,
including 4D and 3M, remain. The default 25-coin universe and TAO inclusion,
log geometry, reversal-colour C1 exception, chronology recovery, early-entry
limits, Telegram recipients, compact alerts and attached charts remain.

## How to judge the next version

Report each direction and timeframe with its denominator. Keep these measures
separate: delivered count, C3 confirmation count, confirmation rate, TP1 and
TP2 before each stop, UNKNOWN/EXPIRED counts, and observations per UTC day.
Compare absolute confirmed and target-reaching opportunities against the
simultaneous hypothetical baseline, not only the accepted percentage.

When estimating expectancy, use the chosen stop and exit convention, realistic
round-trip fees/slippage, and a stated sizing method. Do not count UNKNOWN,
OPEN or EXPIRED paths as profitable. Excluding unknown paths also reduces
coverage and can bias a result; always publish the excluded count.

Review across rising, falling and ranging BTC periods. Evaluate one change
at a time on later, untouched observations. Do not repeatedly tune thresholds
on the same small cohort. These initial defaults should be changed only with
evidence that improves opportunity counts and cost-adjusted outcomes.

Further hypotheses worth testing are relative strength versus BTC for local
pullbacks, separate continuation/reversal cohorts, volume participation,
volatility regimes and liquidity/spread. Funding, open interest and order flow
require validated additional feeds. None should be a mandatory filter merely
because it sounds like confluence, and correlated indicators should not be
counted as independent confirmations.

## Verification and operational limits

170 automated tests pass, including both directions, all original geometry and
timeframe regressions, fresh/stale BTC, live shock versus stabilisation, causal
levels, meaningful reclaim, temporary veto recovery, protective C2 fallback,
full enabled-quality delivery, render-time rechecks, immutable snapshots,
post-C3 tracking, target/stop order, sequence gaps, baseline accounting, private
state migration and delivery isolation. Tests validate implementation, not
market profitability. Source remains Binance spot UTC, with the existing
custom candle anchors; cross-feed TradingView parity is not established.

The saved state and existing mentor subscription were privately backed up
before this release. STATE_BOOTSTRAP_FILE supports a private one-time migration
seed when the local state is missing. It never replaces an existing local
file. Durable STATE_DIR storage is still required for new state to survive
future redeploys; a static bootstrap snapshot is not a persistent database.

Live stream interruptions mark unresolved paths UNKNOWN rather than guessing
what happened during the gap. This sacrifices outcome coverage for honest
accounting; reliable chronological replay is a possible later improvement.
Alerts are observations, and the bot does not execute or size trades.
