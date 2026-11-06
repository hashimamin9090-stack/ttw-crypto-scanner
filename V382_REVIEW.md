# TTW v3.8.2: swept-anchor reclaim for live intraday alerts

The 6 November TAO Spot 2H candidate passed full entry quality at 06:18:44.798
UTC, entry 218.4, SL1 216.783 and TP1 224.952. The observed-turn gate held it
because completed C2 approach pressure was stronger than C1 and no defended
retest was observed. Its frozen hypothetical target-first path later resolved
TP1 before either stop. It was not a delivered trade.

## Change

When the existing fading-pressure direct route is unavailable, a new
`SWEEP_RECLAIM` route can qualify an hourly candidate:

1. The observed C3 extreme sweeps both completed C1/C2 extremes by two ticks.
2. The frozen micro boundary comes from the final minute known before the
   current touch/extreme, as before.
3. A final reversal minute has a reversal body at least half its range and
   closes in the outer quarter. It closes beyond both the micro boundary and
   C2's wick boundary, each by two ticks.
4. Every subsequent final-minute close and the current fresh quote holds that
   stronger boundary. A new extreme or stream-generation change resets proof.

This is a price-action alternative to fading anchor pressure, rather than
unconditionally accepting any minute rejection. The original direct and
defended-retest routes remain available. All upstream geometry, chronology,
colour, entry timing, context and risk rules and final delivery checks apply.
No new API calls or subscriptions are added. Hourly/daily scope is unchanged.
Stored old records remain compatible and keep their original outcome policies.

Production verification also found a v3.8.1 rejection-recording exception:
historical sequence evidence can contain `reason`, which collided with the
explicit `reason` argument to `dict()`. Rejections now merge evidence first and
apply canonical reason/version/close metadata afterwards. This preserves event
evidence and removes the scan/trigger exception. A persisted-record regression
checks the collision and canonical metadata.

## Evidence and limits

TAO swept lows 218.5 and 217.7 to 217.0. Its fixed micro trigger was 217.5;
the stronger reclaimed C2 trigger is 217.9. The 06:15 minute closed at 218.1,
with an 87.5% reversal body and a close at its high. Later completed minutes
held 217.9 through the saved 218.4 quote. The previous gate holds the same quote;
the new route passes it. The fixture includes only completed anchors, the
partial C3 snapshot saved at that quote, and minutes already completed then.

The original 26 saved 5 November quote decisions are unchanged: two of eleven
recorded stopout quotes pass and nine remain held; the one recorded TP1 winner
still passes. All other saved decisions also retain their original route.
Unconditionally allowing direct rejection would instead pass eight of those
eleven stopout quotes. The narrow rule avoids that demonstrated regression.

These are selected quote-time comparisons, not a complete strategy backtest or
a measured improvement in profitability. Historical minute receipt times are
unavailable: the replay models each final minute as known at close plus one
millisecond. Production uses actual receipt times. A held setup can qualify
later at a different price, and this study does not simulate every live quote,
context update, render, network delay or delivery.

The 6 November SNDK 2H example remains blocked by the existing whole-C3
pre-touch impulse policy. Its 14:01:58.497 impulse preceded its 14:50 contact.
There is also a separate primary-stop reward/risk constraint. This release
does not claim to recover that expired entry or send it as a fresh alert.

## Verification

The added regression suite exercises the recorded TAO quote, a synthetic
bearish mirror, a missing sweep of either anchor, insufficient C2 reclaim,
later-close failure, proof-before-close, stale quotes, incomplete anchors,
all 26 old decisions, production scanner wiring and the alert caption. The
full existing suite also covers stream gaps, new extremes, source continuity,
geometry, risk, delivery rechecks, duplicate prevention and persisted outcomes.
Telegram transport remains mocked in tests; no historical signal is resent.
