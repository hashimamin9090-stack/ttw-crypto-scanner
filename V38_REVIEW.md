# TTW v3.8.0 — observed C3 turn and stock location correction

This release follows the user's 5 November review and explicit permission to proceed. The source baseline is main commit a3b5732e86a6b40fcb799ddf612b7f91b3c62513 (v3.7.0). The prepared v3.7.1 stock correction is included. Publication and running-version verification are separate release steps; this document alone is not proof of deployment.

## Chosen implementation

New hourly entries retain the existing pattern, body-clearance, touch-before-impulse, live reversal-colour, minimum reclaim, late-entry, fresh-price, context and risk checks. They additionally need observed evidence of a C3 turn.

**Direct rejection.** Completed C1 must still have an approach-direction body, and C2's approach body must be smaller without already reversing. This is a completed-anchor route choice; a small partial C3 is never called exhaustion merely because it is young. A final one-minute candle must break the last micro boundary known before the current C3 extreme. Its reversal body must occupy at least half its range, its close must lie in the outer quarter, and the break must extend two ticks. Subsequent completed minute closes and the current quote must hold the break.

**Defended retest.** This remains available for every valid anchor colour/approach route, including a C2 that already reversed or pressure that has not faded. One completed minute establishes a rebound. The next minute retreats by at least the greater of two ticks or 10% of the rebound distance, holds at least one tick inside the C3 extreme, and does not extend the rebound peak. A fresh live quote then breaks the fixed rebound peak by two ticks. A third-minute close is not required.

The micro thresholds and route split are engineering hypotheses informed by the lesson's exhaustion/rejection distinction. They are not numerical rules stated by the mentor and have not established a future win rate. Both routes keep the existing early-entry limit: confirmation that arrives after the allowed entry window cannot manufacture a late trade.

C1/C2 TTW anchors and geometry tolerances are unchanged. A new C3 extreme resets the local micro boundary. Missing/stale minute evidence, a changed final minute, gaps, or a discrepancy between a closed minute's extreme and the watched aggregate-trade extreme hold the candidate. These holds do not permanently delete a still-valid setup. Actual geometry/chronology failures keep their existing permanent rejection rules.

The gate applies to hour timeframes. Daily, multi-day, weekly and monthly entry policies remain unchanged. No mandatory C2-midpoint reclaim or higher-timeframe direction veto is introduced. The selected symbols and all 36 timeframes remain scanned.

## SNDK correction

For intraday QCOM, SNDK, MU and USAR perpetuals, a missing historical support/resistance match is context rather than a compulsory entry prerequisite. Own-market opposing levels, target clearance and risk remain active. Metals and other crypto location policies are unchanged.

The SNDK 4H case at the logged 09:35:36 quote of 1009.85 passes the new direct-rejection evidence as well as the corrected location policy in the decision study. Its three lows remain 990.46, 994.33 and 997.14. The known QCOM example still passes bounded geometry and has fading completed-anchor pressure, but its historical minute/trade entry sequence is not independently reconstructed here.

## Data, causality and runtime cost

Each existing feed adds native one-minute kline events to its existing WebSocket. No additional entry-history REST calls are made. Only exchange-final minutes are stored, with the later of server event time, actual receipt time and candle-close-plus-one as their known time. A trade quote must follow the evidence's known time and still pass freshness.

Each symbol retains at most 64 final minutes. Connection and trade-sequence gaps invalidate affected evidence. A missed or changed final candle resets its minute generation. The entry proof is checked before chart generation and again immediately before delivery. Captions remain EARLY C3, with rejection or retest added; this does not claim the setup candle has closed.

Ten thousand bounded evidence evaluations on the saved BTC example took approximately 0.13 seconds locally. This checks implementation cost, not production latency or memory guarantees. No new external dependency is introduced.

## Prospective comparison

The first full-quality quote before the new gate is saved privately as PRE_TURN_FULL_QUALITY_V38. Its own entry, target and both stops are frozen and tracked through the existing outcome system, including gap recovery. This is a pre-render decision comparator, not a claim that the old version would certainly have delivered at exactly that quote.

The comparison cohort is capped at 512 records across feeds. Unresolved paths are preserved; when capacity is occupied by unresolved records, observation skips are counted instead of silently deleting them. Existing legacy observations retain their separate labels. Hypothetical records never generate extra Telegram alerts or orders.

This makes missed opportunities visible alongside delivered results. The current release does not change targets to improve historical statistics, widen already-published stops, or relabel a stopped trade as a win because price later reached a target.

## Recorded decision study

The fixed original cohort contains 26 delivered v3.7.0 alerts. Original SL1-to-TP1 outcome evidence is frozen from the earlier review: eleven STOP, one TP, one EXPIRED, three UNKNOWN and ten UNRESOLVED.

For each original alert quote, the study uses only fully completed same-feed minutes before that quote, saved in fixtures/entry_turn_decisions_20261105.json. The outcome labels are not inputs to the gate.

| Original recorded path | Pass at original quote | Held at original quote |
| --- | ---: | ---: |
| STOP | 2 | 9 |
| TP | 1 | 0 |
| UNKNOWN | 3 | 0 |
| EXPIRED | 1 | 0 |
| UNRESOLVED | 5 | 5 |
| Total | 12 | 14 |

The recorded BTC 3H TP1 winner passes the direct route. The FET 3H and SOL 2H early stopout quotes are held. ZEC 8H and BTC 16H stopouts still pass, demonstrating that the gate is not a complete solution or a post-hoc perfect classifier.

These are quote-time eligibility results, not nine prevented losses or an improved win rate. A held setup can qualify later at a different price. Only one original target winner was resolved in this cohort, and several BTC timeframes describe overlapping episodes. Historical WebSocket receipt latency is unavailable: replay models each final minute as known at close plus one millisecond. C3 extreme time is conservatively the end of the last matching completed minute. The production gate uses actual receipt times and observed trade-extreme times. This is not a complete aggregate-trade, BTC-context, rendering and delivery replay.

## Validation and controls

The final suite passes 397 tests. New cases cover both entry routes and directions, small colour flickers, missing/future/stale minutes, quote-before-proof rejection, native stream routing, gap/correction invalidation, bounded retention, frozen micro boundaries, new C3 extremes, minute/trade discrepancies, completed-anchor-only route selection, the 26 saved decisions, SNDK eligibility, controlled delivery and duplicate prevention, chart-time rechecks, baseline observation and runtime mode controls. Existing QCOM and excessive-contact regressions remain covered. Tests use mock Telegram transport and never place trades.

V38_ENTRY_TURN_MODE defaults to CONFIRM. OBSERVE records the evidence without holding entry. OFF restores the former entry trigger while retaining the stock correction. Quality-disabled legacy mode and daily-and-higher policies are exempt. Previously delivered versions remain compatible and retain their original levels and lifecycle.

Notification grouping, new profit-target schemes and additional swing-pattern alerts are not bundled into this entry experiment. Keeping those changes separate makes the actual effect of the new timing rule assessable.
