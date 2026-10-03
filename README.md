# TTW crypto scanner 2.2

Telegram alerts only. The scanner never places trades.

An alert now requires a third touch followed by a small live rejection. It does
not wait for the setup timeframe's candle close or a minute-candle close.

## Entry rules

1. Use two closed Binance spot candles as wick anchors. Freeze their projected
   third-touch level; never refit the projection to the live third wick.
2. Enforce C2's extreme as a hard boundary. Bullish C3 must never trade below
   C2's low; bearish C3 must never trade above C2's high. Reject outward
   projections that would require breaching that boundary. A later recovery
   does not repair an earlier breach.
3. C3 must actually reach the projection. Its overshoot allowance is the smaller
   of the legacy price-percentage limit and 10% of the smaller C1/C2 wick length
   (with a one-tick floor on the wick-based cap). The hard boundary has no such
   allowance. Live bodies must clear the fixed line, including body edges.
4. Require two consecutive **completed one-second closes after the touch**
   showing a retreat of at least 10% of the smaller anchor wick and at least
   two market ticks. The latest live price must still show that retreat.
5. Skip an entry if its retreat exceeds 25% of the impulse threshold. Reject
   setups that already produced an impulse and returned, ambiguous sequences,
   missing price/tick data, stale observations, or old first touches.

The one-second closes are observed prices, not a two-second delivery promise.
The polling target remains 30 seconds; a scan can take longer. Fast setups
between scans may be skipped rather than alerted late. A live rejection can
still fail after notification. On later polls, the original alert is marked
invalid if C3 breaches C2 or its fixed touch zone. At C3 close, the same alert
receives its final candle-colour/body/geometry verdict.

## Calibration

These defaults are initial calibration, not a validated trading edge:

| Variable | Default | Meaning |
| --- | --- | --- |
| WICK2_TOLERANCE_PCT | 0.05 | Legacy midpoint percentage ceiling; additionally capped by wick size |
| TOUCH_WICK_FRACTION | 0.10 | Touch-zone cap as a fraction of smaller anchor wick |
| REJECTION_WICK_FRACTION | 0.10 | Minimum small rejection as a fraction of smaller anchor wick |
| MAX_OPEN_TO_TOUCH_PCT | 1.0 | Maximum C3 open-to-projection gap |
| IMPULSE_RANGE_FRACTION | 0.5 | Impulse threshold as a fraction of C2 true range |
| MAX_ALERT_DELAY_SECONDS | 90 | Maximum first-touch age |
| MAX_ENTRY_MOVE_FRACTION | 0.25 | Maximum entry retreat as a fraction of impulse threshold |
| SCAN_INTERVAL_SECONDS | 30 | Poll target, not a latency guarantee |

True range is max(C2 high-low, abs(C2 high-C1 close), abs(C2 low-C1 close)).
The impulse threshold retains a floor of four times the new touch tolerance
or 0.01% of C3 open, whichever is larger. Tick sizes come from Binance spot
exchangeInfo PRICE_FILTER. No tick size means no alert.

## Data and verification

The source remains Binance spot. Alerts identify market, timeframe, C3 opening
timestamp and C2 boundary. Charts label all three anchor timestamps in UTC and
show the actual C3 tip separately from the projection. Reported C2 deviation
is now its actual residual against the C1-to-observed-C3 midpoint, rather than
a hardcoded zero. All OHLC snapshots and thresholds remain in the audit log.

3H is aggregated from UTC-aligned hourly bars. Existing longer-timeframe
aggregation anchors are retained; alignment with every TradingView custom
timeframe has not been independently established. The user's apparent chart
mismatch is still under investigation; this update does not claim to change
or fix TradingView's feed.

The scanner reconstructs C3 chronology using hourly bars refined to minute
and second bars. It cannot prove trade order within a single second; ambiguous
cases are skipped. Historical data spans are capped at 5000 hours.

Tests cover early bullish/bearish entries, a touch without rejection, renewed
pressure after a bounce, C2 breaches followed by recovery, stale/unfinished
second bars, late impulses, duplicate suppression, live invalidation updates,
and BTC/ETH audit snapshots from the reported failures. The AVAX regression
checks the later 10.936 overshoot, not an unprovided tick-by-tick replay.
Four earlier hypothetical shapes still pass. The user's new C2 rule supersedes
the two outward-sloping shapes that were previously accepted.

Run tests: `python -m unittest -v test_ttw_touch_first.py`.
Run bot: `python TTW_BOT_V2.py`.

Required settings: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Never commit secrets.
Existing symbols, charts and feedback remain supported. RLUSD is excluded;
TAO is retained. State uses schema version 2. Deployment does not create a
persistent disk; durability still depends on the existing STATE_DIR setup.

API reference:
https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klinecandlestick-data
