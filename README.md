# TTW crypto scanner 3.1

Telegram pattern alerts only; no trade execution. Run `python TTW_BOT_V2.py`.
That deployment entry point starts `ttw_v3.py`; the older module also supplies
shared transport, candles, universe selection, state and chronology recovery.
The V2 detector is retained for historical regression tests, not used live.

## User-confirmed pattern

- Three consecutive relevant wick tips form a coherent line on a **logarithmic**
  price chart. Bullish uses lows; bearish uses highs. The line clears bodies.
- TTWs reverse an opposing approach. The three completed candles before C1 must
  have net movement down for bullish/up for bearish, by at least one tick, with
  at least two of their four high/low transitions in that direction. Mixed colours
  are allowed. This three-candle window is initial calibration, not an optimum.
- Reversal-colour C1 is allowed only after an opposite-colour candle. A doji does
  not establish that opposite colour. Otherwise C1 colour is unrestricted, as is C2.
- Missing or discontinuous approach history defers eligibility. No future price
  is used. Existing 3.0 alerts retain their original stops and lifecycle monitoring.
- C2 may slightly overshoot or fall short of the actual C1-to-C3 line.
- C3 may extend beyond C2's price extreme when the sloping geometry qualifies.
- Observe C3's contact and a small live rejection before a substantial impulse.
  Do not wait for the setup timeframe close. C3's later close is a separate verdict.

## Geometry and initial calibration

Lines are straight in log(price), matching the user's selected TradingView scale.
The C2 reference is sqrt(C1 tip * C3 tip). Its log residual must fit within
20% of the **smaller completed C1/C2 wick's log length**, additionally capped
at log(1.01). This is a bounded initial setting, not a measured/backtested optimum.
Body intersections cannot be repaired by widening tolerance. All relevant wicks
must span at least two market ticks. C3's live log wick must be at least one
quarter of the smaller completed anchor wick to avoid incidental slivers.

The initial contact zone is derived algebraically from those C2 limits:
centre = C2 tip squared / C1 tip; bounds = centre * exp(+/- 2 * allowed C2 residual).
The bot can observe contact anywhere within this bounded zone. It still requires
the actual three-tip geometry and C3 rejection before alerting; the centre alone
does not establish a touch. Excessive outward excursions permanently disqualify
that candidate. The projection is no longer constrained to C2's extreme.

## Live trigger and recovery

One combined public Binance aggregate-trade WebSocket covers the selected pairs.
The standard-library RFC6455 client handles ping/pong, fragmented JSON, payload
limits, reconnection and scheduled connection renewal; no new build dependency.
Anchor candles refresh on the existing REST poll schedule. Candidates are armed
before contact; complete C3 chronology is verified when contact approaches.
Recovery refines ambiguous OHLC event order to minute/second history. Unresolved
order is rejected; missing data retries without establishing a trigger.

After verified zone contact, price must stay at least 10% of the smaller anchor
wick (and two ticks) away from the evolving extreme for two continuous seconds.
Returns towards the extreme reset that clock. New extremes reset it too.
Latest trade must be no older than three seconds. First contact must be within
90 seconds; entry retreat must remain within 25% of the impulse threshold.
Impulse threshold is 0.5*C2 true range, with an eight-tick floor. C3 open-to-zone
gap is bounded by 1.5*C2 true range, replacing the old universal 1% open-gap cap.

Historical first touches cannot be relabelled as fresh. Stream gaps pause signals
and require verified recovery. REST endTime does not truncate current OHLC:
partial hours/minutes are explicitly rebuilt from completed seconds. Startup
and reconnected candidates establish a new observed hold, rather than assume
a historical rejection persisted. These safeguards can miss fast opportunities.

30/60-second hold observations are logged in shadow mode and do not delay alerts.
Heartbeat counters show anchor failures, candidates, verification retries and
rejection reasons. Snapshots retain exact OHLC, geometry, event evidence and
alert-time levels. Observed post-alert favourable/adverse moves are retained;
they are incomplete across outages and do not establish TP/SL hit order.

Stop percentages in the caption are absolute price distances from entry, not
percentages of account equity. They exclude fees and slippage.

## Alert contract

Caption contains pair/timeframe, price at alert, entry, SL1, SL2 and TP range.
Direction is represented by a green/red marker and the chart title. Entry is
the observed trigger price, not a historical C3 opening price.

- SL1: beyond C3's extreme at the alert, with the configured buffer and at least one tick.
- SL2: beyond the outermost relevant wick of the three; always farther than SL1.
  When C3 is outermost, one additional buffer/tick separates the two stops.
- TP for 2H/3H/4H: 3–5% from entry. Above 4H: 5–10% from entry.
  Bullish targets are above entry; bearish targets below. These are requested
  percentage targets, not model predictions or tested profit expectations.
- Stops, entry and targets never move after delivery. SL1 hit marks the original
  message INVALID. At C3 close it receives a compact CONFIRMED/INVALID label.
- The log-scale chart remains attached, with the actual TTW line and SLs.
  Distant TPs stay in the caption so they do not squash the wick detail.
- Valid/invalid feedback and the TradingView chart button remain available.

## Settings

Required: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Never commit credentials.
Existing symbol overrides and TAO inclusion remain. Default universe is 25 pairs.

| Setting | Default |
| --- | --- |
| V3_GEOMETRY_WICK_FRACTION | 0.20 |
| V3_GEOMETRY_PRICE_CAP_PCT | 1.0 |
| V3_REJECTION_HOLD_SECONDS | 2 |
| V3_MAX_OPEN_GAP_RANGE | 1.5 |
| V3_STREAM_URL | wss://stream.binance.com:443 |
| SCAN_INTERVAL_SECONDS | 30 (anchor refresh; live triggers use events) |
| STOP_BUFFER_PCT | 0.10 |
| REJECTION_WICK_FRACTION | 0.10 |
| IMPULSE_RANGE_FRACTION | 0.5 |
| MAX_ALERT_DELAY_SECONDS | 90 |
| MAX_ENTRY_MOVE_FRACTION | 0.25 |

Old WICK2_TOLERANCE_PCT, TOUCH_WICK_FRACTION and MAX_OPEN_TO_TOUCH_PCT are not
V3 geometry settings. Versioned rejections from V2 do not block V3 candidates.
Already-delivered/pending identities still prevent duplicate alerts.

## Validation and limits

Run `python -m unittest discover -v`. Historical V2 tests remain; new V3 checks
cover log geometry, C2 deviations, both colours, outward slopes, tiny live wicks,
body intersections, observed rejection resets, stale/old triggers, event gaps,
partial-history recovery, protective stops, TP boundaries/direction, compact
captions, log chart output, immutable stop edits and uncertain delivery handling.
Screenshot-inspired shapes are illustrative, not exact market-data replays.
Reported BTC/ETH snapshots remain explicit regression cases.

Source is Binance **spot**, UTC. User examples from other feeds teach shape,
not identical prices. Existing custom timeframe aggregation anchors are retained;
parity with every TradingView custom timeframe remains unverified. A log-scale
fix cannot correct exchange/feed or candle-bucket differences.
State schema 2 is preserved. Existing STATE_DIR durability depends on hosting:
this update does not create a paid persistent disk. On ephemeral redeploys,
feedback and stored alert identities can be lost. Historical recovery still
prevents an old observed touch being knowingly issued as a fresh one.

Binance public stream reference:
https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
