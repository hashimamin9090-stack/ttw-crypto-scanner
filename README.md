# TTW crypto scanner 3.2.3

## V3.2.3 colour-trigger consistency

NEAR 2H was watched on 5 October and rejected as STALE_FIRST_TOUCH. V3 no longer
expires a valid candidate merely because its touch is older than 90 seconds.
Completed C1/C2 opposing bodies can establish a local approach with either
wick-tip slope; the existing three-tip geometry remains mandatory. Entry and
post-touch impulse limits now measure the reversal body beyond C3's open, so
a wick rebound can actually reach the colour-change level before triggering.
Previously delivered identities and immutable stops remain compatible.

125 automated tests cover delayed flips in both directions, converging anchor
tips, chronology recovery, delivery, body late entry, prior impulse-and-return,
geometry breaches, stale quotes, C3 expiry, transport and all timeframe context.
Fixtures illustrate the reported shapes; they are not exact TradingView OHLC.
BTC/USD screenshot parity with Binance BTC/USDT has not been established.

Telegram pattern alerts only; no trade execution. Run `python TTW_BOT_V2.py`.
That deployment entry point starts `ttw_v3.py`; the older module also supplies
shared transport, candles, universe selection, state and chronology recovery.
The V2 detector is retained for historical regression tests, not used live.

## User-confirmed pattern

- Three consecutive relevant wick tips form a coherent line on a **logarithmic**
  price chart. Bullish uses lows; bearish uses highs. The line clears bodies.
- TTWs reverse an opposing approach. One route uses three completed candles before C1:
  net movement down for bullish/up for bearish, by at least one tick, with
  at least two of their four high/low transitions in that direction. Mixed colours
  are allowed. Alternatively, C1 and C2 can themselves establish the pullback:
  both must be red for bullish / green for bearish, their combined open-to-close
  movement must oppose the trade by at least one tick. Wick-tip progression is
  assessed separately by geometry: ascending bullish lows and descending bearish
  highs are eligible when the actual three-tip line qualifies.
  This allows a local pullback within a broader trend. Neither route uses C3
  price to manufacture approach evidence. These are initial calibration rules.
- Reversal-colour C1 is allowed only after an opposite-colour candle. A doji does
  not establish that opposite colour. Otherwise C1 colour is unrestricted, as is C2.
- Missing or discontinuous approach history defers eligibility. No future price
  is used. Existing 3.0 alerts retain their original stops and lifecycle monitoring.
- C2 may slightly overshoot or fall short of the actual C1-to-C3 line.
- C3 may extend beyond C2's price extreme when the sloping geometry qualifies.
- Observe C3's contact and live reversal colour before a substantial impulse. Bullish C3 must be green (price above its open); bearish C3 must be red (price below its open). A doji is ineligible.
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
Anchor candles are REST-seeded and then maintained by native UTC candle streams. Candidates are armed
before contact; complete C3 chronology is verified when contact approaches.
Recovery refines ambiguous OHLC event order to minute/second history. Unresolved
order is rejected; missing data retries without establishing a trigger.

After verified zone contact, C3 must turn into the live reversal colour.
There is no timed rejection hold and no wait for the candle to close.
Geometry still requires a visible C3 wick and a line clear of the bodies.
Latest trade must be no older than three seconds. There is no first-contact age
expiry while C3 remains live. Entry must be within 25% of the impulse threshold
**beyond C3's open in the reversal direction**. The wick-to-open rebound is
allowed to form the required colour change; it is not an impulse by itself.
A previously observed post-touch body move of a full impulse threshold beyond
the open permanently rejects the setup, including after it returns. Before
touch, the existing chronological impulse/order guard still applies.
Impulse threshold is 0.5*C2 true range, with an eight-tick floor. C3 open-to-zone
gap is bounded by 1.5*C2 true range, replacing the old universal 1% open-gap cap.

Historical first touches retain their actual timestamps. Stream gaps pause signals
and require verified recovery. REST endTime does not truncate current OHLC:
partial hours/minutes are explicitly rebuilt from completed seconds. Startup
and reconnected candidates require verified chronology and fresh live price
before checking reversal colour. These safeguards can miss fast opportunities.

Heartbeat counters show anchor failures, candidates, verification retries and
rejection reasons. Snapshots retain exact OHLC, geometry, event evidence and
alert-time levels. Observed post-alert favourable/adverse moves are retained;
they are incomplete across outages and do not establish TP/SL hit order.
TTW_CONTEXT logs show the pair, timeframe, candle identity and approach decision,
once per changed decision/bucket. Alert snapshots identify BROADER_REVERSAL or
C1_C2_PULLBACK, so a missed example can be diagnosed without aggregate counters.

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
| V3_MAX_OPEN_GAP_RANGE | 1.5 |
| V3_STREAM_URL | wss://stream.binance.com:443 |
| SCAN_INTERVAL_SECONDS | 30 (anchor refresh; live triggers use events) |
| STOP_BUFFER_PCT | 0.10 |
| REJECTION_WICK_FRACTION | 0.10 |
| IMPULSE_RANGE_FRACTION | 0.5 |
| MAX_ALERT_DELAY_SECONDS | Legacy V2 only; no V3 first-touch expiry |
| MAX_ENTRY_MOVE_FRACTION | 0.25 (body distance beyond C3 open / impulse threshold) |

Old WICK2_TOLERANCE_PCT, TOUCH_WICK_FRACTION and MAX_OPEN_TO_TOUCH_PCT are not
V3 geometry settings. Versioned rejections from V2 do not block V3 candidates.
Already-delivered/pending identities still prevent duplicate alerts.

## Validation and limits

Run `python -m unittest discover -v`. Historical V2 tests remain; new V3 checks
cover log geometry, C2 deviations, both colours, outward slopes, tiny live wicks,
body intersections, live reversal colour, doji rejection, immediate colour flips, stale/old triggers, event gaps,
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
preserves the original touch time and rejects previously impulsed setups.

Binance public stream reference:
https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

V3.1.1 fetches enough base candles for six context/setup candles on every
configured timeframe, including aggregation alignment. New listings may still
lack sufficient history; those candidates remain ineligible until it exists.


## V3.1.2 market-data reliability

All Binance REST requests share one serialized, paced lane (1200 reserved
weight/minute, no startup burst). Reported one-minute IP usage at 3000 triggers
a conservative 62-second pause, accommodating traffic outside this bot. 429/418
responses establish one global cooldown using Retry-After or an absolute
retryAfter timestamp; absent expiry metadata uses increasing fallback waits.
The deadline is stored in the existing state file and survives process restarts
when that directory is retained. Render ephemeral redeploys can lose local state;
a fresh instance must obtain the server's current restriction before proceeding.
No alternate host, IP rotation, or ban bypass is used.

Latest 1h candles cache for 30 seconds, other native intervals for 120 seconds;
all refresh immediately at their native bucket rollover. Completed recovery
candles are reused across verifications; forming history is always refetched.
Caches are bounded and startup is paced. Live trade observation and TTW rules
remain unchanged. New alerts pause during cooldown/recovery; unalerted candidates
are discarded and rebuilt with chronology verification. Sent alert monitors keep
their original stops. A quiet successful scan and a failed/cooling scan both log
health. Usage, cache hits and requests are recorded without response bodies,
credentials or raw ban messages.

Tests exercise concurrent global blocking, deadline persistence/expiry,
header/body fallback, shared-IP headroom, cache rollover, pacing, candidate
recovery and chronology gaps, in addition to the existing pattern tests.


## V3.2.0 reversal colour and 4D

Adds 4D from four UTC daily candles, including the TradingView interval button. Every configured timeframe has automated coverage for sufficient aggregated context. Higher timeframes use the same live reversal-colour rule. This verifies scanner eligibility, not the frequency of live setups or TradingView custom-bucket parity. Existing geometry, opposing approach, first-touch chronology, fresh-price checks, late-entry limits and market cooldown protection remain active.

## V3.2.1 local pullback approach

The October 4 ZEC 2H example exposed the strict pre-C1 window rejecting a local
two-red-candle pullback after a rising approach. C1/C2 may now establish that
opposing approach under the conditions above; the bearish rule is symmetric.
Reversal-colour C1 still requires the existing broader approach and an opposite
previous candle. Geometry, live C3 reversal colour, chronology, freshness and
late-entry limits remain required. Existing 3.2.0 alerts retain lifecycle updates.
Tests cover the recorded ZEC approach with illustrative aligned anchor shapes,
both directions, missing/flat/opposite anchor evidence, no future-price dependence,
and verified pullback delivery. The screenshot is not a full historical replay;
this change establishes eligibility, not proof of the original alert timing.

## V3.2.2 streamed candles and REST recovery

The overnight investigation found two actual Binance 418 blocks followed by
repeated protective `USAGE_HEADROOM` pauses. These precautionary pauses were
clearing all unalerted watches and preventing history verification, despite an
intact aggregate-trade stream. The detection rules are unchanged in this version.

Each of the nine native candle intervals is now subscribed on the same Binance
UTC WebSocket connection as aggregate trades (250 streams for 25 pairs). After
REST seeds the completed context, fresh exchange kline events maintain the
forming candle and roll completed candles forward. Routine setup scans use this
bounded local history instead of downloading every pair/interval repeatedly.
Missing final events, gaps, changed candle opens, stale events and reconnects
force a REST reseed. No guessed OHLC or cross-exchange candle combination is used.

All REST requests still obey the shared usage threshold, actual 418/429 deadlines
and durable cooldowns, with pacing reduced to 600 reserved weight/minute. A
precautionary usage pause prevents REST calls but preserves verified watches:
they can alert only from a fresh, uninterrupted trade stream with verified prefix
chronology. Actual exchange bans still clear new watches and pause alerting until
recovery. Unverified candidates remain unable to send, and deferred verifications
retain their candidate, retry no faster than every 30 seconds, and rebuild touch
evidence from scratch. Clock sync is limited to once per five minutes and expires
after ten minutes. Geometry, C3 colour, freshness, stops, 4D and all higher
timeframes retain their existing rules. Stream cache hits, REST seed counts and
cooldown kind are included in health logs; deferred verification logs identify
the exception class without credentials or raw response bodies.

Tests cover streamed scans for all nine native intervals, final-event rollover
(including calendar months), missing/stale/out-of-order data, snapshot races,
soft-pause delivery only for verified fresh watches, real-ban blocking, candidate
retention and gap recovery. A separate OKX feed was considered; the implementation
does not activate it because its live API could not be validated in the build
environment. Exchange-source labels and independent candle/chronology validation
would be required before introducing that feed.

Official stream and rate-limit references:
https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits
