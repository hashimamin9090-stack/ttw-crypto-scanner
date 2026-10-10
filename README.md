# TTW crypto scanner 3.4.0

## V3.4.0: intraday and higher-timeframe profiles

Production entry checks now use two profiles: below 1D (2H, 3H, 4H, 6H,
8H, 12H, 16H), and 1D upward (all existing daily, weekly and monthly frames).
The shared three-wick geometry, 0.1% C2 price cap, body clearance, opposing
approach, live C3 colour, verified contact chronology and no-prior-impulse
requirements remain. No candle-close wait or timed hold is introduced.

| Check | Below 1D | 1D and above |
| --- | --- | --- |
| Opposing approach | Net move at least 25% of the mean true range of the last five completed context candles, or the tick floor | Original opposing-approach rule |
| C3 body beyond open | Maximum of 3 ticks, 12% of the smaller completed anchor wick, 4% of C2 true range | Maximum of 2 ticks, 8% of the smaller anchor wick, 2% of C2 true range |
| Maximum early-entry body | 40% of the existing impulse distance | 25% of the existing impulse distance |
| Location | Actual contact with a bounded established zone or a shallow sweep and reclaim | Optional context; no matching level alone is not a veto |
| BTC | Existing fresh intraday opposing-expansion veto | Completed BTC context at the setup timeframe; no intraday shock or missing-BTC veto |

The stronger intraday approach is applied to the total opposing move. Both
anchor bodies must oppose for the anchor-pullback route, but neither needs to
be individually large. C3 cannot establish an approach or its strength.

Established levels remain causal and must satisfy the previous completed
reaction/acceptance rules. Zone half-width is the greater of two ticks and
10% of the smaller source/setup mean true range. The C3 tip must contact that
zone, or sweep no farther than twice its half-width and reclaim beyond the
zone on the trade side. A remote daily level cannot qualify an intraday TTW
through the former generous proximity allowance. Higher-timeframe level
evidence uses the same bounded definition, but is supplementary.

The intraday entry-window change is coupled to its minimum reclaim: a
three-tick minimum fits a 3.2-tick allowance when the eight-tick impulse floor
binds. The full-impulse threshold is unchanged; an impulse followed by a
return cannot become a fresh alert. Risk, both protective stops, 1.5R TP1,
1.25R opposing room and known opposing-level target clearance remain required
for both profiles. Targets and stop buffers are unchanged.

BTC daily/3D/weekly/monthly source candles reuse the existing paced/cache/stream
data lane. Higher context uses completed bars only and is labelled UNKNOWN
when unavailable; UNKNOWN is not represented as supporting a trade. The short
BTC trend is never treated as a prerequisite for a weekly reversal.

Entry snapshots record the profile and exact thresholds. Outcome logs include
timeframe/profile; prior versions are labelled LEGACY. A second outcome update
arriving during an awaited message edit stays dirty until that revision has
been logged and saved, including when the edit fails. Existing alerts retain
their frozen setup/rules, deduplication, recipients and outcome tracking.
The existing private 3.2.3 baseline observations continue; they are not a
simultaneous 3.3.1 control and must not be described as one.

Profile thresholds are explicit initial hypotheses, not fitted probabilities
or demonstrated improvements. Compare intraday and higher outcomes separately,
including alert counts, C3 validity and independent target-before-stop paths.
No RSI, volume, cycle-timing or structural-retest gate is added.

With quality enabled, the coupled profile defaults above supersede the legacy
V33_RECLAIM_FRACTION, V33_LEVEL_PROXIMITY_ATR location allowance, and
MAX_ENTRY_MOVE_FRACTION for new deliveries. The latter still defines the
legacy private-baseline window. Existing risk-setting overrides still apply;
V33_QUALITY_ENABLED=false retains the legacy mode. Direct entry_quality calls
without a profile retain the old rules for baseline/regression comparisons.

207 automated cases cover the release, including the previous 186 regressions,
profile boundaries, bullish/bearish delivery, meaningful anchor pullbacks,
minimum-tick feasibility, shallow/deep sweeps, optional higher context, retained
risk/freshness checks, persistence and the asynchronous outcome-edit race.

## V3.3.1 precision and timeframe coverage

This release prioritises fewer, better-qualified alerts while preserving live
C3 entries, eligible slopes, meaningful colour reclaim and the BTC shock veto.

- C2 miss is capped at **0.1% of price**, in addition to the existing 20% of
  smaller anchor-wick limit. An old environment override cannot widen this cap.
- A location level needs a completed closing-price departure of at least
  **0.5 source ATR or two ticks**. Two completed closes accepting through it
  by more than **0.1 ATR or two ticks** retire it. Two completed closes back
  beyond that buffer can re-establish it. Wick sweeps alone do not retire it.
  C1/C2 still cannot create their own historical level. If both accepted
  through an older level, live C3 must reclaim its buffer to use that level.
- Both offered stops must meet the existing **1.5R TP1** and **1.25R opposing
  room** checks. TP1 must also sit before the nearest strong opposing level,
  with clearance of **0.05 setup ATR or two ticks**. Target ranges stay 3–5%
  through 4H, and 5–10% above 4H. Failed price checks retain the live watch.
- Adds native **8H** and custom **16H** (16 complete UTC hourly candles per
  bucket, with a forming live tail). Both use 1D parent levels. All prior
  timeframes through 3M remain. TradingView buttons use 480 and 960 minutes;
  cross-platform custom 16H bucket parity has not been demonstrated.
- Excludes gold tokens XAUT and PAXG from new scans, including symbol overrides.
  Previous delivered setups continue to be tracked with their frozen rules.

The production Config enables these precision checks; direct entry-quality
calls can retain the V3.3.0 checks for baseline/regression comparisons. Existing
persisted alerts, private recipients, feedback and deduplication are preserved.
186 automated tests cover the release, including bullish/bearish live delivery
on 8H/16H, body breaks versus wick sweeps, causal reclaims, target obstacles,
both stop options, stream caching and all previous regression cases.
The new defaults are hypotheses, not demonstrated profitability. Spread/fee
feeds and a relative-strength veto are not added in this release. Outcome
tracking remains price-path accounting rather than net realised P&L.

## V3.3.0 entry context and measurable outcomes

The live three-wick pattern, all timeframes, and early C3 reversal-colour entry
remain. There is no timed hold or candle-close wait. An eligible watch can alert
as soon as its live body reclaims enough of the C3 open and the context below
passes. Temporary context vetoes keep the watch armed; chronology, geometry,
freshness and existing late-entry/impulse limits still apply.

- **Meaningful colour reclaim:** body distance beyond C3 open must reach the
  greater of two ticks, 8% of the smaller completed C1/C2 wick, and 2% of C2 true
  range. Two ticks fits the existing early-entry window when the eight-tick
  impulse floor binds. This is a price condition, not a time delay.
- **Live BTC expansion:** fresh BTC trades and 15m candles veto bullish entries
  during a downside range breakout with a live move of at least 0.9 ATR, or a
  two-completed-bar-plus-live move of at least 1.5 ATR. Bearish rules mirror this.
  Completed 1H direction is recorded for analysis. An older bearish BTC trend
  alone does not block a bullish reversal once acute expansion has stopped.
  Missing/stale BTC data defers alerts, and BTC is checked again after rendering.
- **Established location:** C3 must be near support for a bullish trade or
  resistance for a bearish trade. Levels come from completed range extremes or
  confirmed pivots in up to 32 prior setup candles, excluding C1/C2/C3, or the
  mapped parent timeframe completed before C3 opened. No future pivot is used.
  Proximity is 0.35 source ATR, capped at 0.75 setup ATR, with a two-tick floor.
- **Usable risk:** TP1 must provide at least 1.5 times the frozen C3 stop
  distance, and the nearest known opposing level must be at least 1.25 times
  that distance away. R is a price-risk unit, not account equity or a win odds.
- **Independent outcome ledger:** each delivered alert tracks TP1 and TP2
  before each frozen stop until the end of C3 plus two more setup candles.
  It continues after C3 closes. A target already observed is preserved when a
  later stop occurs. Missing trade IDs, disconnects or process restarts mark
  unresolved paths UNKNOWN; expiration is separate from wins and losses.
- **Baseline observations:** the first eligible legacy entry is also observed
  privately, without a Telegram alert or trade. Its original entry/stops stay
  fixed even if the new watch alerts later. This makes missed winners and
  absolute opportunity counts visible, not just the percentage among sends.

`PATTERN CONFIRMED` is the later C3 colour/geometry/SL1 verdict. It is not a
profitable-trade verdict. Compact `SL1 TP1`, `SL2 HIT`, UNKNOWN and EXPIRED
labels describe independently observed price paths. These are hypothetical
level observations; fees, slippage, fills, sizing and execution are not measured.

Thresholds are initial, testable hypotheses. They have not established an
out-of-sample profit improvement. See [V33_REVIEW.md](V33_REVIEW.md) for the
audit, limits and evaluation criteria.

New audit streams are `TTW_QUALITY`, `TTW_CLOSE`, `TTW_OUTCOME_V33`,
`TTW_SHADOW_ENTRY_V33`, `TTW_SHADOW_CLOSE_V33`, `TTW_SHADOW_OUTCOME_V33`, and
`TTW_FEEDBACK`. Price-path and verdict JSONL files are saved in STATE_DIR.
Feedback logs omit recipient/user IDs. All existing private recipient delivery
and invite rules remain.

| V3.3 setting | Default |
| --- | --- |
| V33_QUALITY_ENABLED | true; false restores legacy entry gates |
| V33_RECLAIM_FRACTION | 0.08 |
| V33_LEVEL_PROXIMITY_ATR | 0.35 |
| V33_MIN_TARGET_R | 1.5 |
| V33_MIN_ROOM_R | 1.25 |
| STATE_BOOTSTRAP_FILE | unset; optional private migration file |

STATE_DIR must be persistent to retain new results, deduplication and recipient
changes across deploys. A private schema-2 bootstrap file can seed a missing
state file during a migration; it never overwrites newer local state. This is
a static recovery snapshot, not ongoing persistence. Remove the bootstrap
setting after durable storage and migration have been verified. Keep state,
subscriber IDs and invitation tokens out of GitHub and the public health server.

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
at log(1.001). This is a bounded initial setting, not a measured/backtested optimum.
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
- SL2 (V3.3): beyond C2's relevant wick when that is on the protective side of
  entry, labelled `SL2 (C2)`. If a sloping pattern has already passed C2, use
  the outermost wick alternative, labelled `SL2 (outer)`. A C2 reference may
  be tighter than SL1; both alternatives are tracked independently.
  Legacy delivered alerts retain their original outer-wick stop.
- TP for 2H/3H/4H: 3–5% from entry. Above 4H: 5–10% from entry.
  Bullish targets are above entry; bearish targets below. These are requested
  percentage targets, not model predictions or tested profit expectations.
- Stops, entry and targets never move after delivery. New alerts distinguish
  observed target/stop paths from the later PATTERN CONFIRMED/INVALID label.
  Legacy alerts retain their original live INVALID and C3 verdict behaviour.
- The log-scale chart remains attached, with the actual TTW line and SLs.
  Distant TPs stay in the caption so they do not squash the wick detail.
- Valid/invalid feedback and the TradingView chart button remain available.

## Settings

Required: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Never commit credentials.
Existing symbol overrides and TAO inclusion remain. Default universe is 25 pairs.

| Setting | Default |
| --- | --- |
| V3_GEOMETRY_WICK_FRACTION | 0.20 |
| V3_GEOMETRY_PRICE_CAP_PCT | 0.1 (hard maximum) |
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

Run `python -m unittest discover -v` (207 tests in this release). Historical V2 tests remain; new V3 checks
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

# Share alerts with a friend

Send `/invite` in the owner's private bot chat. Forward the returned link to one
friend, who opens it and presses Start. The link expires after 24 hours and can
be used once. The friend receives subsequent alerts, chart images, and status
edits alongside the owner, and can submit their own feedback. Existing alerts
are not replayed. `/stop` unsubscribes a friend. The owner can use `/subscribers`
and `/remove CHAT_ID` to inspect or remove additional recipients.

Invites, subscriptions, and per-recipient message IDs are saved in
`STATE_DIR/state-v2.json`. Keep STATE_DIR on persistent storage if subscriptions
must survive Render redeploys; otherwise issue a new invite after a redeploy
that clears local state. Failed or uncertain deliveries are not automatically
resent, to avoid duplicate alerts. Delivery failures for one recipient do not
prevent sending to the others.



