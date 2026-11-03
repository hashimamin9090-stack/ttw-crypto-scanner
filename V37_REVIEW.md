# V3.7.0 lesson implementation and validation record

The source lesson is the user's 83 minute 46 second recording analysed on
3 November 2026. Its important distinctions are exhaustion versus colour,
strict three-consecutive-candle TTWs versus swing-based three-tap structures,
early entry versus confirmed close, multiple timeframe roles, and initial
reaction versus later follow-through. The source review was against v3.6.2,
commit 1009204c15579c02e5d0404e94fbee48c4aeb967.

## Active behaviour

- Early live alerts are explicitly labelled EARLY C3. The entry still needs a
  live reversal colour, verified touch chronology, current bounded geometry,
  body clearance, fresh quote and the existing late-entry/context protections.
- New records carry a lesson policy and source/candle/scale identity. New close
  verdicts assess exhaustion separately from trade-stop events. A weak red
  bullish C3 or weak green bearish C3 is not invalid merely due to colour.
- A later completed C4 is described as extended, progressed or stalled.
  This neither changes an earlier target/stop result nor retargets the trade.
  Failed message edits retry with a bounded attempt count and one saved event.
- Same-instrument, same-feed parent patterns can label an entry as aligned or
  countertrend. Developing parents are labelled developing. A parent does not
  veto an opposite trade. Boundary breaches cannot be undone by recovery, and
  gaps/restarts suppress parent context until complete candle coverage returns.
- An eligible SL1 opportunity is no longer rejected solely because optional
  SL2 reward/risk or room fails. Such SL2 prices are marked reference only.
  Common target clearance and both stops' protective-side validity remain
  required. V37_REQUIRED_STOP=BOTH restores the former alternative-stop gate.

## Observations, not new entry gates

Exhaustion stores body, relevant wick, recovery, directional pressure and anchor
structure measurements. The initial weak-body maximum is 25% of C3 range and
minimum recovery from the relevant extreme is 50%, with a visible two-tick wick.
A strong opposing body has its own route. These are engineering hypotheses,
not mentor-specified cutoffs. They do not filter early entries.

Level reaction describes rejection versus compression at a known level. The
separate three-tap engine uses a 40-bar window, two completed right-hand bars
for pivot confirmation, 0.75 source ATR minimum excursion and 0.25 ATR level
zones, also bounded by tick minima. Distinct swings and frozen range evidence
are mandatory. Reaching a third visit does not manufacture a direction or an
order. Range resolution requires an actually observed completed close through
one of the original boundaries. Gapped histories remain unknown.

Candidate storage is bounded at 512 records and swing storage at 128 shared
across Spot and Futures. Events log at discovery/phase change, not on every
trade. No extra market-history requests or heavy dependencies are introduced.
The existing symbol list, 36 timeframes and 3H+ focus priority are preserved.

Linear/log midpoint differences are measured in the audit without selecting
whichever scale rescues a candidate. Production geometry/rendering stays log.
The lesson's apparent chart scale and custom 69D candle phase remain unverified,
so neither is silently made a new trading default.

## Validation scope

The previous 346-case baseline passes before changes. New tests cover synthetic
lesson-style weak C3s and strong opposing bodies, real swing separation,
future-bar exclusion, frozen ranges, missing history, mirrored resolution,
parent gap/invalidation/restart behaviour, independent close/stop/follow-through
events, message-edit retry, source labels, stop-policy clearance and bounded
retention. Existing recorded QCOM eligibility and the four excessive BTC/SNDK
rejections remain covered. Only the release-version expectation in an existing
test is updated, with v3.6.2 compatibility explicitly checked.

The final local suite contains 374 passing tests. A 1,000-iteration bounded
synthetic swing scan took about 0.16 seconds in the development environment;
this is an implementation check, not a production latency guarantee. Parent
invalidation uses a per-symbol index rather than scanning every retained
candidate on each trade event.

Synthetic cases are not exact lesson OHLC or evidence of profitability. The QCOM
fixture establishes geometry, not the complete historical entry sequence. New
first-seen times are actual discovery times, never a historical candle opening
recast as an early alert. Old delivered records are not reclassified.

Improvement in future trade results requires retained live observations and
untouched replay episodes. No new win-rate claim is made by this release.
